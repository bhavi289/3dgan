from __future__ import absolute_import, division, print_function

import tensorflow as tf
from tensorflow.contrib.framework.python.ops.arg_scope import arg_scope
import math
import hem



# TODO Add summaries for all GPUs

class experimental_sampler(hem.ModelPlugin):
    name = 'experimental_sampler'

    @staticmethod
    def arguments():
        args = {
            '--g_sparsity': {
                'action': 'store_true',
                'default': False,
                'help': 'Set this to add a sparsity term (for the encoder output/activation) to the loss function.'
                },
            '--g_rmse': {
                'action': 'store_true',
                'default': False,
                'help': 'Setting this will add a RMSE loss term to the generator.'
                },
            '--g_arch': {
                'type': str,
                'default': 'E2',
                'help': 'Architecture to use for the generator. Options are...'
                },
            '--d_arch': {
                'type': str,
                'default': 'E2',
                'help': 'Architecture to use for the generator. Options are...'
                },
            '--examples': {
                'type': int,
                'default': 64,
                'help': 'Number of image summary examples to use.'}
            }
        return args


    # with tf.variable_scope('input_pipeline'):
    #     x, handle, iterators = hem.get_dataset_tensors(args)
    #     # calculate number of iterations per epoch and get the dataset handles
    #     # train
    #     iter_per_epoch = args.epoch_size
    #     if args.epoch_size <= 0:
    #         iter_per_epoch = int(iterators['train']['n'] / (args.batch_size * args.n_gpus))
    #     training_handle = iterators['train']['x'].string_handle()
    #     # validate
    #     validate_iter_per_epoch = int(iterators['validate']['n'] / (args.batch_size * args.n_gpus))
    #     validation_handle = iterators['validate']['x'].string_handle()
    #     # test. this is an optional dataset
    #     test_iter_per_epoch = 0
    #     test_handle = None
    #     if 'test' in iterators:
    #         test_iter_per_epoch = int(iterators['test']['n'] / (args.batch_size * args.n_gpus))
    #         test_handle = iterators['test']['x'].string_handle()

    # x, handle, iterators = hem.get_dataset_tensors(args)


    @hem.default_to_cpu
    def __init__(self, x_y, estimator, args):
        # init/setup
        g_opt = hem.init_optimizer(args)
        d_opt = hem.init_optimizer(args)
        g_tower_grads = []
        d_tower_grads = []
        global_step = tf.train.get_global_step()



        # sess = tf.Session(config = tf.ConfigProto(allow_soft_placement=True, log_device_placement=False))
        # new_saver = tf.train.import_meta_graph(
        #     '/mnt/research/projects/autoencoders/workspace/improved_sampler/experimentE/meandepth.e1/checkpoint-4.meta', import_scope='estimator')
        # new_saver.restore(sess, tf.train.latest_checkpoint('/mnt/research/projects/autoencoders/workspace/improved_sampler/experimentE/meandepth.e1'))
        #
        # # estimator_vars = tf.get_collection(tf.GraphKeys.GLOBAL_VARIABLES)
        # # print('estimator_vars:', estimator_vars)
        #
        # # print('all ops!')
        # # for op in tf.get_default_graph().get_operations():
        # #     if 'l8' in str(op.name):
        # #         print(str(op.name))
        #
        #
        # # sess.graph
        # # graph = tf.get_default_graph()
        # estimator_tower0 = sess.graph.as_graph_element('estimator/tower_0/model/l8/add').outputs[0]
        # estimator_tower1 = sess.graph.as_graph_element('estimator/tower_1/model/l8/add').outputs[0]
        # self.estimator_placeholder = sess.graph.as_graph_element('estimator/input_pipeline/Placeholder') #.outputs[0]
        # print('PLACEHOLDER:', self.estimator_placeholder)
        # print('estimator_tower0:', estimator_tower0)
        # print('estimator_tower1:', estimator_tower1)
        # estimator_tower0 = tf.stop_gradient(estimator_tower0)
        # estimator_tower1 = tf.stop_gradient(estimator_tower1)
        # sess.close()


        # foreach gpu...
        for x_y, scope, gpu_id in hem.tower_scope_range(x_y, args.n_gpus, args.batch_size):
            with tf.variable_scope('input_preprocess'):
                # split inputs and rescale
                x = hem.rescale(x_y[0], (0, 1), (-1, 1))
                y = hem.rescale(x_y[1], (0, 1), (-1, 1))


                # if args.g_arch == 'E2':
                y = hem.crop_to_bounding_box(y, 16, 16, 32, 32)
                y = tf.reshape(y, (-1, 1, 32, 32))
                x_loc = x_y[2]
                y_loc = x_y[3]
                scene_image = x_y[4]
                mean_depth = tf.stop_gradient(estimator.output_layer)
                # print('mean_depth_layer:', estimator.output_layer)
                # mean_depth = estimator(scene_image)
                # mean_depth = tf.expand_dims(mean_depth, axis=-1)
                # mean_depth = tf.expand_dims(mean_depth, axis=-1)

                mean_depth_channel = tf.stack([mean_depth] * 64, axis=2)
                mean_depth_channel = tf.stack([mean_depth_channel] * 64, axis=3)
                # mean_depth_channel = tf.squeeze(mean_depth_channel)
                # print('mean_depth_layer99:', mean_depth_channel)

                # mean_depth_channel = tf.ones_like(x_loc) * mean_depth




                # print('x', x)
                # print('x_loc', x_loc)
                # print('y_loc', y_loc)
                # print('mean_depth_channel', mean_depth_channel)
                x = tf.concat([x, x_loc, y_loc, mean_depth_channel], axis=1)
                # print('x shape:',  x)

                # create repeated image tensors for sampling
                x_sample = tf.stack([x[0]] * args.batch_size)
                y_sample = tf.stack([y[0]] * args.batch_size)
                # shuffled x for variance calculation
                x_shuffled = tf.random_shuffle(x)
                y_shuffled = y
                # noise vector for testing
                x_noise = tf.random_uniform(tf.stack([tf.Dimension(args.batch_size), x.shape[1], x.shape[2], x.shape[3]]), minval=-1.0, maxval=1.0)

            g_arch = {'E2': experimental_sampler.generatorE2}
            d_arch = {'E2': experimental_sampler.discriminatorE2}

            # create model
            with tf.variable_scope('generator'):
                g_func = g_arch[args.g_arch]
                g = g_func(x, args, reuse=(gpu_id > 0))
                g_sampler = g_func(x_sample, args, reuse=True)
                g_shuffle = g_func(x_shuffled, args, reuse=True)
                g_noise = g_func(x_noise, args, reuse=True)
            with tf.variable_scope('discriminator'):
                d_func = d_arch[args.d_arch]
                d_real, d_real_logits = d_func(x, y, args, reuse=(gpu_id > 0))
                d_fake, d_fake_logits = d_func(x, g, args, reuse=True)

            # calculate losses
            g_loss, d_loss = experimental_sampler.loss(d_real, d_real_logits, d_fake, d_fake_logits, x, g, y, None, args, reuse=(gpu_id > 0))
            # calculate gradients
            g_params = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, 'generator')
            d_params = tf.get_collection(tf.GraphKeys.TRAINABLE_VARIABLES, 'discriminator')
            g_tower_grads.append(g_opt.compute_gradients(g_loss, var_list=g_params))
            d_tower_grads.append(d_opt.compute_gradients(d_loss, var_list=d_params))
            # only need one batchnorm update (ends up being updates for last tower)
            batchnorm_updates = tf.get_collection(tf.GraphKeys.UPDATE_OPS, scope)
            # TODO: do we need to do this update for batchrenorm? for instance renorm?

        # average and apply gradients
        g_grads = hem.average_gradients(g_tower_grads, check_numerics=args.check_numerics)
        d_grads = hem.average_gradients(d_tower_grads, check_numerics=args.check_numerics)
        g_apply_grads = g_opt.apply_gradients(g_grads, global_step=global_step)
        d_apply_grads = d_opt.apply_gradients(d_grads, global_step=global_step)

        # add summaries
        hem.summarize_losses()
        hem.summarize_gradients(g_grads, name='g_gradients')
        hem.summarize_gradients(d_grads, name='d_gradients')
        hem.summarize_layers('g_activations', [l for l in tf.get_collection('conv_layers') if 'generator' in l.name], montage=True)
        hem.summarize_layers('d_activations', [l for l in tf.get_collection('conv_layers') if 'discriminator' in l.name], montage=True)
        experimental_sampler.montage_summaries(x, y, g, x_sample, y_sample, g_sampler, x_noise, g_noise, x_shuffled, y_shuffled, g_shuffle, args)
        experimental_sampler.sampler_summaries(y_sample, g_sampler, g_noise, y_shuffled, g_shuffle, args)

        # training ops
        with tf.control_dependencies(batchnorm_updates):
            self.g_train_op = g_apply_grads
        self.d_train_op = d_apply_grads
        self.all_losses = hem.collection_to_dict(tf.get_collection('losses'))

    def train(self, sess, args, feed_dict):
        # _ = sess.run(self.d_train_op, feed_dict=feed_dict)
        # _ = sess.run(self.g_train_op, feed_dict=feed_dict)
        # results = sess.run(self.all_losses, feed_dict=feed_dict)

        # old_dict = feed_dict
        # new_dict = {'estimator/input_pipeline/Placeholder': feed_dict['handle']}
        # new_dict = {self.estimator_placeholder: feed_dict['handle']}
        # feed_dict = {**feed_dict, **new_dict}
        
        _, _, results = sess.run([self.d_train_op, self.g_train_op, self.all_losses], feed_dict=feed_dict)
        return results


    @staticmethod
    def generatorE2(x, args, reuse=False):
        with tf.variable_scope('encoder', reuse=reuse), \
             arg_scope([hem.conv2d],
                       reuse=reuse,
                       stride=2,
                       padding='SAME',
                       filter_size=5,
                       init=tf.contrib.layers.xavier_initializer,
                       activation=tf.nn.relu):  # 64x64x3
            noise = tf.random_uniform([args.batch_size, 1, 64, 64], minval=-1.0, maxval=1.0)
            x = tf.concat([x, noise], axis=1)  # 64x64x4
            e1 = hem.conv2d(x, 7, 64, name='e1')  # 32x32x64
            e2 = hem.conv2d(e1, 64, 128, name='e2')  # 16x16x128
            e3 = hem.conv2d(e2, 128, 256, name='e3')  # 8x8x256
            e4 = hem.conv2d(e3, 256, 512, name='e4')  # 4x4x512
            e5 = hem.conv2d(e4, 512, 1024, filter_size=4, padding='VALID', name='e5')  # 1x1x1024

        with tf.variable_scope('decoder', reuse=reuse), \
             arg_scope([hem.deconv2d, hem.conv2d],
                       reuse=reuse,
                       stride=2,
                       init=tf.contrib.layers.xavier_initializer,
                       padding='SAME',
                       filter_size=5,
                       activation=lambda x: hem.lrelu(x, leak=0.2)):  # 1x1x1024
            y = hem.deconv2d(e5, 1024, 512, output_shape=(args.batch_size, 512, 4, 4), filter_size=4, padding='VALID',
                             name='d1')  # 4x4x512
            y = tf.concat([y, e4], axis=1)  # 4x4x1024
            y = hem.deconv2d(y, 1024, 256, output_shape=(args.batch_size, 256, 8, 8), name='d2')  # 8x8x256
            y = tf.concat([y, e3], axis=1)  # 8x8x512
            y = hem.deconv2d(y, 512, 128, output_shape=(args.batch_size, 128, 16, 16), name='d3')  # 16x16x128
            y = tf.concat([y, e2], axis=1)  # 16x16x256
            y = hem.deconv2d(y, 256, 64, output_shape=(args.batch_size, 64, 32, 32), name='d4')  # 32x32x64
            y = tf.concat([y, e1], axis=1)  # 32x32x128
            y = hem.conv2d(y, 128, 1, stride=1, filter_size=1, activation=tf.nn.tanh, name='d5')  # 31x31x1
        return y


    @staticmethod
    def discriminatorE2(x, y, args, reuse=False):
        with arg_scope([hem.conv2d],
                       reuse=reuse,
                       activation=lambda x: hem.lrelu(x, leak=0.2),
                       init=tf.contrib.layers.xavier_initializer,
                       padding='SAME',
                       filter_size=5,
                       stride=2):  # x = 64x64x3, y = 32x32x1
            with tf.variable_scope('rgb_path'):
                h1 = hem.conv2d(x, 6, 64, name='hx1')  # 32x32x64
                h1 = hem.conv2d(h1, 64, 128, name='hx2')  # 16x16x128
                h1 = hem.conv2d(h1, 128, 256, name='hx3')  # 8x8x256
                h1 = hem.conv2d(h1, 256, 512, name='hx4')  # 4x4x512
                h1 = hem.conv2d(h1, 512, 1024, padding='VALID', filter_size=4, name='hx5')  # 1x1x1024
            with tf.variable_scope('depth_path'):
                h2 = hem.conv2d(y, 1, 128, name='hy1')  # 16x16x128
                h2 = hem.conv2d(h2, 128, 256, name='hy2')  # 8x8x256
                h2 = hem.conv2d(h2, 256, 512, name='hy3')  # 4x4x512
                h2 = hem.conv2d(h2, 512, 1024, filter_size=4, padding='VALID', name='hy4')  # 1x1x1024
            with tf.variable_scope('combined_path'):
                h = tf.concat([h1, h2], axis=1)  # 1x1x2048
                h = hem.conv2d(h, 2048, 1024, stride=1, filter_size=1, padding='SAME', name='h1')  # 1x1x1024
                h = hem.conv2d(h, 1024, 512, stride=1, filter_size=1, padding='SAME', name='h2')  # 1x1x512
                h = hem.conv2d(h, 512, 256, stride=1, filter_size=1, padding='SAME', name='h3')  # 1x1x256
                h = hem.conv2d(h, 256, 128, stride=1, filter_size=1, padding='SAME', name='h4')  # 1x1x1x128
                h = hem.conv2d(h, 128, 64, stride=1, filter_size=1, padding='SAME', name='h5')  # 1x1x1x64
                h = hem.conv2d(h, 64, 1, stride=1, filter_size=1, padding='SAME', name='h6',
                               activation=None)  # 1x1x1x64
        # output, logits
        return tf.nn.sigmoid(h), h

    @staticmethod
    def loss(d_real, d_real_logits, d_fake, d_fake_logits, x, g, x_depth, q, args, reuse=False):
        def xentropy(logits, labels):
            return tf.nn.sigmoid_cross_entropy_with_logits(logits=logits, labels=labels)

        with tf.variable_scope('loss'):
            g = hem.rescale(g, (-1, 1), (0, 1))
            x_depth = hem.rescale(x_depth, (-1, 1), (0, 1))
            rmse_loss = hem.rmse(x_depth, g)
            l1_loss = tf.reduce_mean(tf.abs(x_depth - g), name='l1')

            # losses
            with tf.variable_scope('generator'):
                g_fake = tf.reduce_mean(xentropy(d_fake_logits, tf.ones_like(d_fake)), name='g_fake')
                # if args.g_sparsity:
                #     layers = tf.get_collection('conv_layers')
                #     for l in layers:
                #         if 'e5' in l.name:
                #             sparsity_term = tf.nn.zero_fraction(l, name='sparsity_term')
                #     lambda_term = 1.0
                #     if args.g_rmse:
                #         g_total = tf.identity(g_fake - lambda_term * sparsity_term + rmse_loss, name='g_total')
                #     else:
                #         g_total = tf.identity(g_fake - lambda_term * sparsity_term, name='g_total')
                # elif args.g_rmse:
                #     g_total = tf.identity(g_fake + rmse_loss, name='g_total')

            with tf.variable_scope('discriminator'):
                d_real = tf.reduce_mean(xentropy(d_real_logits, tf.ones_like(d_real)), name='d_real')
                d_fake = tf.reduce_mean(xentropy(d_fake_logits, tf.zeros_like(d_fake)), name='d_fake')
                d_total = tf.identity(d_real + d_fake, name='d_total')
            # only add these to the collection once
            if not reuse:
                hem.add_to_collection('losses', [g_fake, d_real, d_fake, d_total, rmse_loss, l1_loss])
                # if args.g_sparsity:
                #     hem.add_to_collection('losses', [g_total, sparsity_term])
                # elif args.g_rmse:
                #     hem.add_to_collection('losses', [g_total])
        # if args.g_sparsity or args.g_rmse:
        #     return g_total, d_total
        # else:
        return g_fake, d_total

    # summaries
    ############################################

    @staticmethod
    def montage_summaries(x, y, g,
                          x_sample, y_sample, g_sampler,
                          x_noise, g_noise,
                          x_shuffled, y_shuffled, g_shuffle,
                          args):
        n = math.floor(math.sqrt(args.examples))

        if args.g_arch in ['E2']:
            x, x_loc, y_loc, mean_vec = tf.split(x, num_or_size_splits=[3, 1, 1, 1], axis=1)
            x_noise, x_loc_noise, y_loc_noise, mean_vec_noise = tf.split(x_noise, num_or_size_splits=[3, 1, 1, 1], axis=1)
            x_shuffled, x_loc_shuffled, y_loc_shuffled, mean_vec_shuffled = tf.split(x_shuffled, num_or_size_splits=[3, 1, 1, 1], axis=1)
            x_sample, x_loc_sample, y_loc_sample, mean_vec_sample = tf.split(x_sample, num_or_size_splits=[3, 1, 1, 1], axis=1)
            with tf.variable_scope('model'):
                hem.montage(x_loc, num_examples=args.examples, height=n, width=n, name='x_loc', colorize=True)
                hem.montage(y_loc, num_examples=args.examples, height=n, width=n, name='y_loc', colorize=True)
                hem.montage(mean_vec, num_examples=args.examples, height=n, width=n, name='mean_vec', colorize=True)

        with tf.variable_scope('montage_preprocess'):
            g = tf.reshape(g, tf.shape(y))  # reattach
            g = hem.rescale(g, (-1, 1), (0, 1))
            y = hem.rescale(y, (-1, 1), (0, 1))
            g_sampler = tf.reshape(g_sampler, tf.shape(y))  # reattach
            g_sampler = hem.rescale(g_sampler, (-1, 1), (0, 1))
            y_sample = hem.rescale(y_sample, (-1, 1), (0, 1))

        # add image montages
        with arg_scope([hem.montage],
                       num_examples=args.examples,
                       height=n,
                       width=n,
                       colorize=True):
            with tf.variable_scope('model'):
                hem.montage(x, name='images')
                hem.montage(y, name='real_depths')
                hem.montage(g, name='fake_depths')
            with tf.variable_scope('sampler'):
                hem.montage(x_sample, name='images')
                hem.montage(y_sample, name='real_depths')
                hem.montage(g_sampler, name='fake_depths')
            with tf.variable_scope('shuffled'):
                hem.montage(x_shuffled, name='images')
                hem.montage(g_shuffle, name='fake_depths')
            with tf.variable_scope('noise'):
                hem.montage(x_noise, name='images')
                hem.montage(g_noise, name='fake_depths')

    @staticmethod
    def sampler_summaries(y_sample, g_sampler, g_noise, y_shuffled, g_shuffle, args):
        def per_image_statistics(y, y_hat, scope_name):
            with tf.variable_scope(scope_name):
                # reduce over all dimensions but the batch.
                rmse = tf.reduce_mean(tf.sqrt(tf.square(y - y_hat)), axis=[1, 2, 3]) * 10.0
                tf.summary.scalar('rmse/mean', tf.reduce_mean(rmse))
                tf.summary.scalar('rmse/min', tf.reduce_min(rmse))

        with tf.variable_scope('moments'):
            with tf.variable_scope('sampler'):
                g_sampler = tf.reshape(g_sampler, y_sample.shape)  # reattach
                hem.summarize_moments(g_sampler, 'moments', args)
            with tf.variable_scope('noise'):
                g_noise = tf.reshape(g_noise, y_sample.shape)  # reattach
                hem.summarize_moments(g_noise, 'moments', args)
            with tf.variable_scope('shuffled'):
                hem.summarize_moments(g_shuffle, 'moments', args)
        with tf.variable_scope('per_image_metrics'):
            per_image_statistics(y_sample, g_sampler, 'sampler')
            per_image_statistics(y_shuffled, g_shuffle, 'shuffled')
            per_image_statistics(y_sample, g_noise, 'noise')
