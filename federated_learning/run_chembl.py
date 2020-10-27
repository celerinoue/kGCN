#!/usr/bin/env python
import click
import numpy as np
import tensorflow as tf
import tensorflow_federated as tff
from tensorflow.keras import optimizers
import tensorflow_federated as tff

import kgcn.layers as layers

from datasets.chembl import load_data


def build_model(max_n_atoms, max_n_types, protein_max_seqlen, length_one_letter_aa):
    input_adjs = tf.keras.Input(shape=(1, max_n_atoms, max_n_atoms), name='adjs', sparse=False)
    input_features = tf.keras.Input(shape=(max_n_atoms, max_n_types), name='features')    
    input_protein_seq = tf.keras.Input(shape=(protein_max_seqlen), name='protein_seq')

    # for graph
    h = layers.GraphConvFL(64, 1)(input_features, input_adjs)
    h = tf.keras.layers.ReLU()(h)
    h = layers.GraphConvFL(64, 1)(h, input_adjs)
    h = tf.keras.layers.ReLU()(h)
    h = layers.GraphGather()(h)
    
    # for protein sequence
    h_seq = tf.keras.layers.Embedding(length_one_letter_aa, 128, input_length=protein_max_seqlen)(input_protein_seq)
    h_seq = tf.keras.layers.GlobalAveragePooling1D()(h_seq)
    h_seq = tf.keras.layers.Dense(64, activation='relu')(h_seq)

    # concat
    h = tf.keras.layers.Concatenate()([h, h_seq])
    logits = tf.keras.layers.Dense(1, activation='sigmoid')(h)
    return tf.keras.Model(inputs=[input_adjs, input_features, input_protein_seq], outputs=logits)


def client_data(source, n, batch_size, epochs):
    return source.create_tf_dataset_for_client(source.client_ids[n]).repeat(epochs).batch(batch_size)


@click.command()
@click.option('--rounds', default=20, help='the number of updates of the centeral model')
@click.option('--clients', default=2, help='the number of clients')
@click.option('--subsets', default=7, help='the number of subsets')
@click.option('--epochs', default=10, help='the number of training epochs in client traning.')
@click.option('--batchsize', default=32, help='the number of batch size.')
@click.option('--lr', default=0.2, help='learning rate for the central model.')
@click.option('--clientlr', default=0.001, help='learning rate for client models.')
def main(rounds, clients, subsets, epochs, batchsize, lr, clientlr):
    MAX_N_ATOMS = 150
    MAX_N_TYPES = 120
    PROTEIN_MAX_SEQLEN = 750
    LENGTH_ONE_LETTER_AA = len('XACDEFGHIKLMNPQRSTVWY')
    #subset_ratios = [8/10, 1/10, 1/10]
    #Load simulation data.
    chembl_train = load_data(MAX_N_ATOMS, MAX_N_TYPES, PROTEIN_MAX_SEQLEN,
                             subsets)

    # # Pick a subset of client devices to participate in training.
    all_data = [client_data(chembl_train, n, batchsize, epochs) for n in range(4)]
    
    example_element = list(all_data[0])
    # Wrap a Keras model for use with TFF.
    def model_fn():
        model = build_model(MAX_N_ATOMS, MAX_N_TYPES, PROTEIN_MAX_SEQLEN,
                            LENGTH_ONE_LETTER_AA)
        return tff.learning.from_keras_model(
            model,
            loss=tf.keras.losses.BinaryCrossentropy(),
            metrics=[tf.keras.metrics.BinaryAccuracy(), tf.keras.metrics.AUC()],
            input_spec=all_data[0].element_spec)
    
    # Simulate a few rounds of training with the selected client devices.
    trainer = tff.learning.build_federated_averaging_process(
        model_fn,
        client_optimizer_fn=lambda: tf.keras.optimizers.Adam(clientlr),
        server_optimizer_fn=lambda: tf.keras.optimizers.Adam(learning_rate=lr))
    
    state = trainer.initialize()
    evaluation = tff.learning.build_federated_evaluation(model_fn)

    for k in range(clients):
        test_data = [all_data[k],]
        val_data = [all_data[k+1],]
        train_data = [d for idx, d in enumerate(all_data) if not idx in [k, k+1]]
        print(f'{k} round ->')
        for round_num in range(rounds):
            state, metrics = trainer.next(state, train_data)
            print(f'{round_num:03d} metrics===>\n', metrics)
            val_metrics = evaluation(state.model, val_data)
            print(f'{round_num:03d} val_metrics\n', val_metrics)
        test_metrics = evaluation(state.model, test_data)
        print(f'{round_num:03d} test_metrics\n', test_metrics)

    
if __name__ == '__main__':
    main()