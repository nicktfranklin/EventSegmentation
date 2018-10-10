import pandas as pd
import numpy as np
from models.sem import SEM
from models.event_models import *
from scipy.special import logsumexp
from tqdm import tqdm
import sys


def z_score(x):
    return (x - np.mean(x)) / np.std(x)


def convert_type_token(event_types):
    tokens = [0]
    for ii in range(len(event_types)-1):
        if event_types[ii] == event_types[ii+1]:
            tokens.append(tokens[-1])
        else:
            tokens.append(tokens[-1] + 1)
    return tokens
def get_event_duration(event_types, frequency=30):
    tokens = convert_type_token(event_types)
    n_tokens = np.max(tokens)+1
    lens = []
    for ii in range(n_tokens):
        lens.append(np.sum(np.array(tokens) == ii))
    return np.array(lens, dtype=float) / frequency


def segment_compare(event_sequence, comparison_data, f_class, f_opts, lmda=10 ** 5, alfa=10 ** -1):
    """
    :param event_sequence: (NxD np.array) the sequence of N event vectors in D dimensions
    :param f_class: (EventModel) the event model class
    :param f_opts:  (dict) all of the keyword arguments for the event model class
    :param lmda:    (float) stickiness parameter in sCRP
    :param alfa:    (float) concentration parameter in sCRP
    :return:
    """

    Omega = {
        'lmda': lmda,  # Stickiness (prior)
        'alfa': alfa,  # Concentration parameter (prior)
        'f_class': f_class,
        'f_opts': f_opts
    }

    sem_model = SEM(**Omega)
    sem_model.run(event_sequence, k=event_sequence.shape[0], leave_progress_bar=True)

    # compare the model segmentation to human data via regression and store the output

    prediction_error = z_score(-sem_model.results.log_loss)

    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ #
    # calculate the boundary probability
    e_hat = sem_model.results.e_hat.copy()
    log_post = sem_model.results.log_prior.copy() + sem_model.results.log_like.copy()
    # normalize
    log_post0 = log_post - np.tile(np.max(log_post, axis=1).reshape(-1, 1), (1, log_post.shape[1]))
    log_post0 -= np.tile(logsumexp(log_post0, axis=1).reshape(-1, 1), (1, log_post.shape[1]))

    boundary_probability = [0]
    for ii in range(1, log_post0.shape[0]):
        idx = range(log_post0.shape[0])
        idx.remove(e_hat[ii - 1])
        boundary_probability.append(logsumexp(log_post0[ii, idx]))
    boundary_probability = np.array(boundary_probability)
    # ~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~~ #

    # bin the prediction error!
    def prep_compare_pe(bin_size):

        frame_time = np.arange(1, len(prediction_error) + 1) / 30.0

        index = np.arange(0, np.max(frame_time), bin_size)
        prediction_error_binned = []
        for t in index:
            prediction_error_binned.append(prediction_error[(frame_time >= t) & (frame_time < (t + bin_size))].sum())
        prediction_error_binned = pd.Series(prediction_error_binned, index=index)

        yw_binned = []
        for t in index:
            l = comparison_data[(comparison_data.index > t) &
                                 (comparison_data.index < (t + bin_size))]
            if l.empty:
                yw_binned.append(0)
            else:
                yw_binned.append(l.max().values[0])

        yw_binned = pd.Series(np.array(yw_binned), index=index)
        X = prediction_error_binned.values
        y = yw_binned.values
        return X, y

    # bin the boundary probability!
    def prep_compare_boundprob(bin_size):

        frame_time = np.arange(1, len(boundary_probability) + 1) / (30.0 )

        index = np.arange(0, np.max(frame_time), bin_size)
        boundary_probability_binned = []
        for t in index:
            boundary_probability_binned.append(
                # note: this operation is equivalent to the log of the average boundary probability in the window
                logsumexp(boundary_probability[(frame_time >= t) & (frame_time < (t + bin_size))]) - \
                np.log(bin_size * 30.)
            )
        boundary_probability_binned = pd.Series(boundary_probability_binned, index=index)

        # bin the subject data
        yw_binned = []
        for t in index:
            l = comparison_data[(comparison_data.index > t) &
                                 (comparison_data.index < (t + bin_size))]
            if l.empty:
                yw_binned.append(0)
            else:
                yw_binned.append(l.max().values[0])

        yw_binned = pd.Series(np.array(yw_binned), index=index)
        X = boundary_probability_binned.values
        y = yw_binned.values
        return X, y

    def get_r2(x, y):
        x = (x - np.mean(x)) / np.std(x)
        y = (y - np.mean(y)) / np.std(y)
        return np.corrcoef(x, y)[0][1] ** 2

    # loop through and correlate the model prediction error
    r2s_pe = []
    bins = np.arange(0.20, 8.1, 0.2)
    n_permute = 500
    r2s_pe_rand = np.zeros((n_permute, len(bins)))

    for ii, b in tqdm(enumerate(bins), desc='Prediction Error') :
        x, y = prep_compare_pe(b)
        r2s_pe.append(get_r2(x, y))
        for jj in range(n_permute):
            np.random.shuffle(x)
            r2s_pe_rand[jj, ii] = get_r2(x, y)

    # loop through and correlate the model boundary probability

    r2s_bp = []
    r2s_bp_rand = np.zeros((n_permute, len(bins)))

    for ii, b in tqdm(enumerate(bins), desc='Boundary Probability'):
        x, y = prep_compare_boundprob(b)
        r2s_bp.append(get_r2(x, y))
        for jj in range(n_permute):
            np.random.shuffle(x)
            r2s_bp_rand[jj, ii] = get_r2(x, y)

    return pd.DataFrame({
        'Bin Size': bins,
        'PE Model r2': np.array(r2s_pe),
        'PE Permutation Mean': r2s_pe_rand.mean(axis=0),
        'PE Permutation Std': r2s_pe_rand.std(axis=0),
        'PE Permutation p-value': np.mean(r2s_pe_rand - (np.tile(np.array(r2s_pe), (n_permute, 1))) > 0, axis=0),
        'BP Model r2': np.array(r2s_bp),
        'BP Permutation Mean': r2s_bp_rand.mean(axis=0),
        'BP Permutation Std': r2s_bp_rand.std(axis=0),
        'BP Permutation p-value': np.mean(r2s_bp_rand - (np.tile(np.array(r2s_bp), (n_permute, 1))) > 0, axis=0),
        'Event Length (avg)': np.mean(get_event_duration(e_hat)),
        'Event Length (std)': np.std(get_event_duration(e_hat)),
        'LogLoss': np.sum(sem_model.results.log_loss)
    })


def main(df0=10, scale0=0.3, l2_regularization=0.3, dropout=0.1, t=10, data_path=None,
         optimizer=None, output_id_tag=None, n_epochs=100):

    if data_path is None:
        data_path = './'

    # load the raw data
    Z = np.load('./video_color_Z_embedded_64.npy')

    # the "Sax" movie is from time slices 0 to 5537
    event_sequence = Z[range(0, 5537), :]
    # sax = Z[range(0, 500), :]

    comparison_data = pd.read_csv('./zachs_2006_young_unwarned.csv', header=-1)
    comparison_data.set_index(0, inplace=True)

    output_tag = '_df0_{}_scale0_{}_l2_{}_do_{}'.format(df0, scale0, l2_regularization, dropout)
    if output_id_tag is not None:
        output_tag += output_id_tag

    ####### DP-GMM Events #########
    f_class = Gaussian
    f_opts = dict(var_df0=df0, var_scale0=scale0)
    lmbd_gmm = 0  # we want no stickiness as a control!
    args = [event_sequence, comparison_data, f_class, f_opts, lmbd_gmm]

    sys.stdout.write('Running DP-GMM\n')
    res = segment_compare(*args)
    res['EventModel'] = ['DP-GMM'] * len(res)
    res.to_pickle(data_path + 'EventR2_DPGMM' + output_tag + '.pkl')

    ####### Gaussian Random Walk #########
    f_class = GaussianRandomWalk
    f_opts = dict(var_df0=df0, var_scale0=scale0)
    args = [event_sequence, comparison_data, f_class, f_opts]

    sys.stdout.write('Running Random Walk\n')
    res = segment_compare(*args)
    res['EventModel'] = ['RandomWalk'] * len(res)
    res.to_pickle(data_path + 'EventR2_RandWalk' + output_tag + '.pkl')

    ####### LDS Events #########
    f_class = KerasLDS
    f_opts = dict(var_df0=df0, var_scale0=scale0, l2_regularization=l2_regularization,
                optimizer=optimizer, n_epochs=n_epochs,
                )
    args = [event_sequence, comparison_data, f_class, f_opts]

    sys.stdout.write('Running LDS\n')
    res = segment_compare(*args)
    res['EventModel'] = ['LDS'] * len(res)
    res.to_pickle(data_path + 'EventR2_LDS' + output_tag + '.pkl')

    ####### MLP Events #########
    f_class = KerasMultiLayerPerceptron
    f_opts = dict(var_df0=df0, var_scale0=scale0, l2_regularization=l2_regularization,
                  dropout=dropout, optimizer=optimizer,  n_epochs=n_epochs)
    args = [event_sequence, comparison_data, f_class, f_opts]

    res = []
    sys.stdout.write('Running MLP\n')
    res = segment_compare(*args)
    res['EventModel'] = ['MLP'] * len(res)
    res.to_pickle(data_path + 'EventR2_MLP' + output_tag + '.pkl')

    ####### SRN Events #########
    f_class = KerasRecurrentMLP
    f_opts = dict(var_df0=df0, var_scale0=scale0, l2_regularization=l2_regularization,
                  dropout=dropout, t=t, optimizer=optimizer,  n_epochs=n_epochs)
    args = [event_sequence, comparison_data, f_class, f_opts]

    sys.stdout.write('Running RNN\n')
    res = segment_compare(*args)
    res['EventModel'] = ['RNN'] * len(res)
    res.to_pickle(data_path + 'EventR2_RNN' + output_tag + '.pkl')

    ####### GRU #########
    f_class = KerasGRU
    f_opts = dict(var_df0=df0, var_scale0=scale0, l2_regularization=l2_regularization,
                  dropout=dropout, t=t, optimizer=optimizer,  n_epochs=n_epochs)
    args = [event_sequence, comparison_data, f_class, f_opts]

    sys.stdout.write('Running GRU\n')
    res = segment_compare(*args)
    res['EventModel'] = ['GRU'] * len(res)
    res.to_pickle(data_path + 'EventR2_GRU' + output_tag + '.pkl')

    ####### LSTM #########
    f_class = KerasLSTM
    f_opts = dict(var_df0=df0, var_scale0=scale0, l2_regularization=l2_regularization,
                  dropout=dropout, t=t, optimizer=optimizer,  n_epochs=n_epochs)
    args = [event_sequence, comparison_data, f_class, f_opts]

    sys.stdout.write('Running LSTM\n')
    res = segment_compare(*args)
    res['EventModel'] = ['LSTM'] * len(res)
    res.to_pickle(data_path + 'EventR2_LSTM' + output_tag + '.pkl')

    sys.stdout.write('Done!\n')


if __name__ == "__main__":
    data_path = 'data/video_results/'

    main(df0=10, scale0=0.3, l2_regularization=0.0, dropout=0.5, t=10, data_path=data_path,
    optimizer='sgd', output_id_tag="sgd")

    main(df0=10, scale0=0.3, l2_regularization=0.0, dropout=0.5, t=10, data_path=data_path,
         output_id_tag="adam")
