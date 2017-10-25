import logging
import os

from keras.callbacks import Callback
from keras.layers import Activation, TimeDistributed, Dense, RepeatVector, Dropout, recurrent
from keras.models import Sequential, load_model
import numpy as np
from numpy.random import rand, randint, choice

from shcomplete.seq2seq_prediction import get_config, get_vocabulary, Vocabulary


def get_chars(path_to_vocab):
    """
    Return the list of all characters found in the vocabulary.
    """
    chars = set()
    with open(path_to_vocab, "r") as f:
        for line in f:
            line = list(line.rstrip())
            chars.update(set(line))
    return list(chars)


def create_mistakes(chars, command, vocab_trie, level_of_noise):
    """
    Add artificial spelling mistakes into a single command.
    The number of mistakes is proportional to the level of noise we choose.
    """
    mistakes = {0: "swap", 1: "delition", 2: "addition", 3: "transposition"}
    ind = randint(len(mistakes))
    prefix = vocab_trie.longest_prefix(command)[0]

    if prefix is not None and rand() < level_of_noise:
        char_pos = randint(len(prefix))
        if mistakes[ind] == "swap":
            command = command[:char_pos] + choice(chars) + command[char_pos + 1:]
        elif mistakes[ind] == "delition":
            command = command[:char_pos] + command[char_pos + 1:]
        elif mistakes[ind] == "addition":
            command = command[:char_pos] + choice(chars) + command[char_pos:]
        elif mistakes[ind] == "transposition":
            char_pos = randint(len(command) - 1)
            command = (command[:char_pos] + command[char_pos + 1] + command[char_pos] +
                       command[char_pos + 2:])
    return command


def generate_model(chars, gConfig):
    """
    Generate the model.
    """
    model = Sequential()
    for layer_number in range(gConfig["input_layers"]):
        model.add(recurrent.LSTM(gConfig["hidden_layers"], input_shape=(None, len(chars)),
                                 return_sequences=layer_number + 1 < gConfig["input_layers"]))
        model.add(Dropout(gConfig["amount_of_dropout"]))

    model.add(RepeatVector(gConfig["max_cmd_len"]))
    for _ in range(gConfig["output_layers"]):
        model.add(recurrent.LSTM(gConfig["hidden_layers"], return_sequences=True))
        model.add(Dropout(gConfig["amount_of_dropout"]))

    model.add(TimeDistributed(Dense(len(chars))))
    model.add(Activation('relu'))
    model.compile(loss='mse', optimizer='adam')
    return model


def generate_input(chars, command, vocab_trie, gConfig):
    """
    Generate a tuple (misspelled_command, true_command) to train the model.
    The misspelled_command is generated by adding artificial mistakes.abs
    Padding is added to feed the model with fixed length sequences of characters.
    """
    misspelled_command = create_mistakes(chars, command, vocab_trie, gConfig["level_of_noise"])
    misspelled_command += gConfig["padding"] * (gConfig["max_cmd_len"] - len(misspelled_command))
    command += gConfig["padding"] * (gConfig["max_cmd_len"] - len(command))
    return misspelled_command, command


def sample_prediction(model, chars, X, y, max_cmd_len, inverted):
    """
    Select 10 misspelled commands and print the current correction of the model.
    """
    seq2seq = Seq2seq(chars)
    print()
    for _ in range(10):
        ind = randint(0, len(X))
        rowX = X[np.array([ind])]
        rowy = y[np.array([ind])]
        preds = model.predict_classes(rowX)
        true_command = seq2seq.decode(rowy[0], max_cmd_len)
        model_correction = seq2seq.decode(preds[0], max_cmd_len, reduction=False)
        if inverted:
            misspelled_command = seq2seq.decode(rowX[0], max_cmd_len, inverted=inverted)
            print('Command misspelled :', misspelled_command[::-1])
        else:
            print('Q :', misspelled_command)
        print('True command :', true_command)
        print("Correction predicted :", model_correction)
        print('---')
    return model_correction


class Seq2seq(object):

    def __init__(self, chars):
        self.chars = sorted(set(chars))
        self.char_indices = dict((c, i) for i, c in enumerate(self.chars))
        self.indices_char = dict((i, c) for i, c in enumerate(self.chars))

    def encode(self, commands, max_cmd_len):
        """
        Encode commands into numpy arrays.
        """
        X = np.zeros((len(commands), max_cmd_len, len(self.chars)), dtype=np.bool)
        for i, cmd in enumerate(commands):
            for j, char in enumerate(cmd):
                try:
                    X[i, j, self.char_indices[char]] = 1
                except KeyError:
                    # Padding
                    pass
        return X

    def decode(self, X, max_cmd_len, reduction=True, inverted=False):
        """
        Decode the numpy array X and return the corresponding command.
        """
        command = ""
        if reduction:
            X = X.argmax(axis=-1)
            if inverted:
                begin_of_command = np.amin(np.nonzero(X))
                for i in range(begin_of_command, max_cmd_len):
                    command += self.indices_char[X[i]]
            else:
                end_of_command = np.amax(np.nonzero(X))
                for i in range(end_of_command + 1):
                    command += self.indices_char[X[i]]
            return command
        elif len(np.nonzero(X)[0]) != 0:
            end_of_command = np.amax(np.nonzero(X))
            for i in range(end_of_command + 1):
                command += self.indices_char[X[i]]
        return command


class OnEpochEndCallback(Callback):

    def __init__(self, path_to_vocab, file_delimiter, path_to_corpus, models_directory, gConfig):
        self.gConfig = gConfig
        self.vocab = path_to_vocab
        self.file_delimiter = file_delimiter
        self.corpus = path_to_corpus
        self.models_directory = models_directory

    def on_epoch_end(self, epoch, logs=None):
        """
        Apply the prediction of the model to a batch of data at the end of each epoch.
        """
        chars = get_chars(self.vocab)
        X, y = next(generator(chars, self.corpus, self.file_delimiter, self.vocab, self.gConfig))
        sample_prediction(self.model, chars, X, y,
                          self.gConfig["max_cmd_len"],
                          self.gConfig["inverted"])
        path_to_model = os.path.join(self.models_directory, "keras_spell_e{}.h5".format(epoch))
        if epoch % 100 == 0:
            self.model.save(path_to_model)


def generator(chars, corpus, file_delimiter, path_to_vocab, gConfig):
    """
    An epoch finishes when  samples_per_epoch samples have been seen by the model.
    """
    seq2seq = Seq2seq(chars)
    vocab = Vocabulary(path_to_vocab)
    vocab_trie = vocab.trie(path_to_vocab)
    commands = []
    misspelled_commands = []
    while True:
        with open(corpus) as f:
            lines = [cmd.strip() for cmd in f.readlines()
                     if cmd != file_delimiter and len(cmd) <= gConfig["max_cmd_len"]]
            for _ in range(gConfig["batch_size"]):
                command = choice(lines)
                misspelled_command, command = generate_input(chars, command, vocab_trie, gConfig)
                commands.append(command)
                assert len(command) == gConfig["max_cmd_len"]
                if gConfig["inverted"]:
                    misspelled_command = misspelled_command[::-1]
                misspelled_commands.append(misspelled_command)
            assert len(commands) == len(misspelled_commands)
            assert len(commands) == gConfig["batch_size"]
            X = seq2seq.encode(misspelled_commands, gConfig["max_cmd_len"])
            y = seq2seq.encode(commands, gConfig["max_cmd_len"])
            commands = []
            misspelled_commands = []
            yield X, y


def train_corr(args, log_level=logging.INFO):
    """
    Train the model and show the progress of the prediction at each epoch.
    """
    _log = logging.getLogger("training")
    _log.setLevel(log_level)

    if args.from_model:
        model = load_model(args.from_model)
    else:
        global gConfig
        gConfig = get_config(args.config_file)
        chars = get_chars(args.vocabulary)
        model = generate_model(chars, gConfig)

    ON_EPOCH_END_CALLBACK = OnEpochEndCallback(args.vocabulary, args.file_delimiter,
                                               args.corpus, args.models_directory, gConfig)
    model.fit_generator(generator(chars, args.corpus, args.file_delimiter,
                                  args.vocabulary, gConfig),
                        samples_per_epoch=gConfig["steps_per_epoch"],
                        nb_epoch=gConfig["nb_epoch"],
                        callbacks=[ON_EPOCH_END_CALLBACK, ],
                        validation_data=None)
