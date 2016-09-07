'''
Neural network architecture.
The input to the policy network is a 19 x 19 x 48 image stack consisting of
48 feature planes. The first hidden layer zero pads the input into a 23 x 23
image, then convolves k filters of kernel size 5 x 5 with stride 1 with the
input image and applies a rectifier nonlinearity. Each of the subsequent
hidden layers 2 to 12 zero pads the respective previous hidden layer into a
21 x 21 image, then convolves k filters of kernel size 3 x 3 with stride 1,
again followed by a rectifier nonlinearity. The final layer convolves 1 filter
of kernel size 1 x 1 with stride 1, with a different bias for each position,
and applies a softmax function. The match version of AlphaGo used k = 192
filters; Fig. 2b and Extended Data Table 3 additionally show the results
of training with k = 128, 256 and 384 filters.

The input to the value network is also a 19 x 19 x 48 image stack, with an
additional binary feature plane describing the current colour to play.
Hidden layers 2 to 11 are identical to the policy network, hidden layer 12
is an additional convolution layer, hidden layer 13 convolves 1 filter of
kernel size 1 x 1 with stride 1, and hidden layer 14 is a fully connected
linear layer with 256 rectifier units. The output layer is a fully connected
linear layer with a single tanh unit.
'''
import functools
import math
import operator
import os
import tensorflow as tf

import features
import go
import utils

class PolicyNetwork(object):
    def __init__(self, num_input_planes, k=32, num_int_conv_layers=3):
        self.num_input_planes = num_input_planes
        self.k = k
        self.num_int_conv_layers = num_int_conv_layers
        self.test_summary_writer = None
        self.training_summary_writer = None
        self.session = tf.Session()
        self.set_up_network()
        self.set_up_summaries()

    def set_up_network(self):
        # a global_step variable allows epoch counts to persist through multiple training sessions
        global_step = tf.Variable(0, name="global_step", trainable=False)
        x = tf.placeholder(tf.float32, [None, go.N, go.N, self.num_input_planes])
        y = tf.placeholder(tf.float32, shape=[None, go.N ** 2])

        #convenience functions for initializing weights and biases
        # http://neuralnetworksanddeeplearning.com/chap3.html#weight_initialization
        def _product(numbers):
            return functools.reduce(operator.mul, numbers)

        def _weight_variable(shape, name):
            # If shape is [5, 5, 20, 32], then each of the 32 output planes
            # has 5 * 5 * 20 inputs.
            number_inputs_added = _product(shape[:-1])
            stddev = 1 / math.sqrt(number_inputs_added)
            return tf.Variable(tf.truncated_normal(shape, stddev=stddev), name=name)

        def _conv2d(x, W):
            return tf.nn.conv2d(x, W, strides=[1,1,1,1], padding="SAME")

        # initial conv layer is 5x5
        W_conv_init = _weight_variable([5, 5, self.num_input_planes, self.k], name="W_conv_init")
        h_conv_init = tf.nn.relu(_conv2d(x, W_conv_init), name="h_conv_init")

        # followed by a series of 3x3 conv layers
        W_conv_intermediate = []
        h_conv_intermediate = []
        _current_h_conv = h_conv_init
        for i in range(self.num_int_conv_layers):
            W_conv_intermediate.append(_weight_variable([3, 3, self.k, self.k], name="W_conv_inter" + str(i)))
            h_conv_intermediate.append(tf.nn.relu(_conv2d(_current_h_conv, W_conv_intermediate[-1]), name="h_conv_inter" + str(i)))
            _current_h_conv = h_conv_intermediate[-1]

        W_conv_final = _weight_variable([1, 1, self.k, 1], name="W_conv_final")
        b_conv_final = tf.Variable(tf.constant(0, shape=[go.N ** 2], dtype=tf.float32), name="b_conv_final")
        h_conv_final = _conv2d(h_conv_intermediate[-1], W_conv_final)
        output = tf.nn.softmax(tf.reshape(h_conv_final, [-1, go.N ** 2]) + b_conv_final)

        log_likelihood_cost = -tf.reduce_mean(tf.reduce_sum(tf.mul(tf.log(output), y), reduction_indices=[1]))

        train_step = tf.train.AdamOptimizer(1e-4).minimize(log_likelihood_cost, global_step=global_step)
        was_correct = tf.equal(tf.argmax(output, 1), tf.argmax(y, 1))
        accuracy = tf.reduce_mean(tf.cast(was_correct, tf.float32))

        weight_summaries = tf.merge_summary([
            tf.histogram_summary(weight_var.name, weight_var)
            for weight_var in [W_conv_init] +  W_conv_intermediate + [W_conv_final, b_conv_final]],
            name="weight_summaries"
        )
        activation_summaries = tf.merge_summary([
            tf.histogram_summary(act_var.name, act_var)
            for act_var in [h_conv_init] + h_conv_intermediate + [h_conv_final]],
            name="activation_summaries"
        )
        saver = tf.train.Saver()

        # save everything to self.
        for name, thing in locals().items():
            if not name.startswith('_'):
                setattr(self, name, thing)

    def set_up_summaries(self):
        # See summarize() for why things are set up this way
        accuracy_summary = tf.placeholder(tf.float32, [])
        cost_summary = tf.placeholder(tf.float32, [])
        _accuracy = tf.scalar_summary("accuracy", accuracy_summary)
        _cost = tf.scalar_summary("log_likelihood_cost", cost_summary)
        accuracy_summaries = tf.merge_summary([_accuracy, _cost], name="accuracy_summaries")
        # save everything to self.
        for name, thing in locals().items():
            if not name.startswith('_'):
                setattr(self, name, thing)

    def initialize_logging(self, tensorboard_logdir):
        self.test_summary_writer = tf.train.SummaryWriter(os.path.join(tensorboard_logdir, "test"), self.session.graph)
        self.training_summary_writer = tf.train.SummaryWriter(os.path.join(tensorboard_logdir, "training"), self.session.graph)

    def summarize(self, accuracy, cost):
        # Accuracy and cost cannot be calculated with the full test dataset
        # in one pass, so they must be computed in batches. Unfortunately,
        # the built-in TF summary nodes cannot be told to aggregate multiple
        # executions. Therefore, we aggregate the accuracy/cost ourselves at
        # the python level, and then shove it through the accuracy/cost summary
        # nodes to generate the appropriate summary protobufs for writing.
        return self.session.run(self.accuracy_summaries, 
            feed_dict={self.accuracy_summary: accuracy, self.cost_summary: cost})

    def initialize_variables(self, save_file=None):
        if save_file is None:
            self.session.run(tf.initialize_all_variables())
        else:
            self.saver.restore(self.session, save_file)

    def get_global_step(self):
        return self.session.run(self.global_step)

    def save_variables(self, save_file):
        self.saver.save(self.session, save_file)

    def train(self, training_data, batch_size=32):
        num_minibatches = training_data.data_size // batch_size
        aggregate_accuracy, aggregate_cost = 0, 0
        for i in range(num_minibatches):
            batch_x, batch_y = training_data.get_batch(batch_size)
            _, accuracy, cost = self.session.run(
                [self.train_step, self.accuracy, self.log_likelihood_cost],
                feed_dict={self.x: batch_x, self.y: batch_y})
            aggregate_accuracy += accuracy
            aggregate_cost += cost

        avg_accuracy = aggregate_accuracy / num_minibatches
        avg_cost = aggregate_cost / num_minibatches
        global_step = self.get_global_step()
        aggregate_accuracy, aggregate_cost = 0, 0
        print("Step %d training data accuracy: %g; cost: %g" % (global_step, avg_accuracy, avg_cost))
        if self.training_summary_writer is not None:
            activation_summaries = self.session.run(
                self.activation_summaries,
                feed_dict={self.x: batch_x, self.y: batch_y})
            accuracy_summaries = self.summarize(avg_accuracy, avg_cost)
            self.training_summary_writer.add_summary(activation_summaries, global_step)
            self.training_summary_writer.add_summary(accuracy_summaries, global_step)


    def run(self, position):
        'Return a sorted list of (probability, move) tuples'
        processed_position = features.DEFAULT_FEATURES.extract(position)
        probabilities = self.session.run(self.output, feed_dict={self.x: processed_position[None, :]})[0]
        move_probs = [(prob, utils.unflatten_coords(i)) for i, prob in enumerate(probabilities)]
        return sorted(move_probs, reverse=True)

    def check_accuracy(self, test_data, batch_size=128):
        num_minibatches = test_data.data_size // batch_size
        weight_summaries = self.session.run(self.weight_summaries)

        aggregate_accuracy, aggregate_cost = 0, 0
        for i in range(num_minibatches):
            batch_x, batch_y = test_data.get_batch(batch_size)
            accuracy, cost = self.session.run(
                [self.accuracy, self.log_likelihood_cost],
                feed_dict={self.x: batch_x, self.y: batch_y})
            aggregate_accuracy += accuracy
            aggregate_cost += cost

        avg_accuracy = aggregate_accuracy / num_minibatches
        avg_cost = aggregate_cost / num_minibatches
        accuracy_summaries = self.summarize(avg_accuracy, avg_cost)
        global_step = self.get_global_step()
        print("Step %s test data accuracy: %g; cost: %g" % (global_step, avg_accuracy, avg_cost))

        if self.test_summary_writer is not None:
            self.test_summary_writer.add_summary(weight_summaries, global_step)
            self.test_summary_writer.add_summary(accuracy_summaries, global_step)

