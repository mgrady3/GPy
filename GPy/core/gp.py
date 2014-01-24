# Copyright (c) 2012, GPy authors (see AUTHORS.txt).
# Licensed under the BSD 3-clause license (see LICENSE.txt)

import numpy as np
import pylab as pb
import warnings
from .. import kern
from ..util.plot import gpplot, Tango, x_frame1D, x_frame2D
from ..util.linalg import dtrtrs
from model import Model
from parameterization import ObservableArray
from .. import likelihoods
from ..likelihoods.gaussian import Gaussian
from ..inference.latent_function_inference import exact_gaussian_inference

class GP(Model):
    """
    General purpose Gaussian process model

    :param X: input observations
    :param Y: output observations
    :param kernel: a GPy kernel, defaults to rbf+white
    :param likelihood: a GPy likelihood
    :rtype: model object

    .. Note:: Multiple independent outputs are allowed using columns of Y


    """
    def __init__(self, X, Y, kernel, likelihood, inference_method=None, name='gp'):
        super(GP, self).__init__(name)

        assert X.ndim == 2
        self.X = ObservableArray(X)
        self.num_data, self.input_dim = self.X.shape

        assert Y.ndim == 2
        self.Y = ObservableArray(Y)
        assert Y.shape[0] == self.num_data
        _, self.output_dim = self.Y.shape

        assert isinstance(kernel, kern.kern)
        self.kern = kernel

        assert isinstance(likelihood, likelihoods.Likelihood)
        self.likelihood = likelihood

        #find a sensible inference method
        if inference_method is None:
            if isinstance(likelihood, likelihoods.Gaussian):
                inference_method = exact_gaussian_inference.ExactGaussianInference()
        else:
            inference_method = expectation_propagation
            print "defaulting to ", inference_method, "for latent function inference"
        self.inference_method = inference_method

        self.add_parameter(self.kern, gradient=self.dL_dtheta_K)
        self.add_parameter(self.likelihood, gradient=lambda:self.posterior.dL_dtheta_lik)

        self.parameters_changed()

    def parameters_changed(self):
        super(GP, self).parameters_changed()
        self.K = self.kern.K(self.X)
        self.posterior = self.inference_method.inference(self.K, self.likelihood, self.Y)

    def log_likelihood(self):
        return self.posterior.log_marginal

    def dL_dtheta_K(self):
        return self.kern.dK_dtheta(self.posterior.dL_dK, self.X)

    def _raw_predict(self, _Xnew, which_parts='all', full_cov=False, stop=False):
        """
        Internal helper function for making predictions, does not account
        for normalization or likelihood

        full_cov is a boolean which defines whether the full covariance matrix
        of the prediction is computed. If full_cov is False (default), only the
        diagonal of the covariance is returned.

        """
        Kx = self.kern.K(_Xnew, self.X, which_parts=which_parts).T
        LiKx, _ = dtrtrs(self.posterior._woodbury_chol, np.asfortranarray(Kx), lower=1)
        mu = np.dot(Kx.T, self.posterior._woodbury_vector)
        if full_cov:
            Kxx = self.kern.K(_Xnew, which_parts=which_parts)
            var = Kxx - tdot(LiKx.T)
        else:
            Kxx = self.kern.Kdiag(_Xnew, which_parts=which_parts)
            var = Kxx - np.sum(LiKx*LiKx, 0)
            var = var.reshape(-1, 1)
        return mu, var

    def predict(self, Xnew, which_parts='all', full_cov=False, **likelihood_args):
        """
        Predict the function(s) at the new point(s) Xnew.

        :param Xnew: The points at which to make a prediction
        :type Xnew: np.ndarray, Nnew x self.input_dim
        :param which_parts:  specifies which outputs kernel(s) to use in prediction
        :type which_parts: ('all', list of bools)
        :param full_cov: whether to return the full covariance matrix, or just the diagonal
        :type full_cov: bool
        :returns: mean: posterior mean,  a Numpy array, Nnew x self.input_dim
        :returns: var: posterior variance, a Numpy array, Nnew x 1 if full_cov=False, Nnew x Nnew otherwise
        :returns: lower and upper boundaries of the 95% confidence intervals, Numpy arrays,  Nnew x self.input_dim


           If full_cov and self.input_dim > 1, the return shape of var is Nnew x Nnew x self.input_dim. If self.input_dim == 1, the return shape is Nnew x Nnew.
           This is to allow for different normalizations of the output dimensions.

        """
        # normalize X values
        mu, var = self._raw_predict(Xnew, full_cov=full_cov, which_parts=which_parts)

        # now push through likelihood
        mean, var, _025pm, _975pm = self.likelihood.predictive_values(mu, var, full_cov, **likelihood_args)
        return mean, var, _025pm, _975pm

    def posterior_samples_f(self,X,size=10,which_parts='all',full_cov=True):
        """
        Samples the posterior GP at the points X.

        :param X: The points at which to take the samples.
        :type X: np.ndarray, Nnew x self.input_dim.
        :param size: the number of a posteriori samples to plot.
        :type size: int.
        :param which_parts: which of the kernel functions to plot (additively).
        :type which_parts: 'all', or list of bools.
        :param full_cov: whether to return the full covariance matrix, or just the diagonal.
        :type full_cov: bool.
        :returns: Ysim: set of simulations, a Numpy array (N x samples).
        """
        m, v = self._raw_predict(X, which_parts=which_parts, full_cov=full_cov)
        v = v.reshape(m.size,-1) if len(v.shape)==3 else v
        if not full_cov:
            Ysim = np.random.multivariate_normal(m.flatten(), np.diag(v.flatten()), size).T
        else:
            Ysim = np.random.multivariate_normal(m.flatten(), v, size).T

        return Ysim

    def posterior_samples(self,X,size=10,which_parts='all',full_cov=True,noise_model=None):
        """
        Samples the posterior GP at the points X.

        :param X: the points at which to take the samples.
        :type X: np.ndarray, Nnew x self.input_dim.
        :param size: the number of a posteriori samples to plot.
        :type size: int.
        :param which_parts: which of the kernel functions to plot (additively).
        :type which_parts: 'all', or list of bools.
        :param full_cov: whether to return the full covariance matrix, or just the diagonal.
        :type full_cov: bool.
        :param noise_model: for mixed noise likelihood, the noise model to use in the samples.
        :type noise_model: integer.
        :returns: Ysim: set of simulations, a Numpy array (N x samples).
        """
        Ysim = self.posterior_samples_f(X, size, which_parts=which_parts, full_cov=full_cov)
        if isinstance(self.likelihood, Gaussian):
            noise_std = np.sqrt(self.likelihood._get_params())
            Ysim += np.random.normal(0,noise_std,Ysim.shape)
        elif isinstance(self.likelihood, Gaussian_Mixed_Noise):
            assert noise_model is not None, "A noise model must be specified."
            noise_std = np.sqrt(self.likelihood._get_params()[noise_model])
            Ysim += np.random.normal(0,noise_std,Ysim.shape)
        else:
            Ysim = self.likelihood.noise_model.samples(Ysim)

        return Ysim

    def plot_f(self, *args, **kwargs):
        """
        Plot the GP's view of the world, where the data is normalized and before applying a likelihood.

        This is a convenience function: we simply call self.plot with the
        argument use_raw_predict set True. All args and kwargs are passed on to
        plot.

        see also: gp.plot
        """
        kwargs['plot_raw'] = True
        self.plot(*args, **kwargs)

    def plot(self, plot_limits=None, which_data_rows='all',
            which_data_ycols='all', which_parts='all', fixed_inputs=[],
            levels=20, samples=0, fignum=None, ax=None, resolution=None,
            plot_raw=False,
            linecol=Tango.colorsHex['darkBlue'],fillcol=Tango.colorsHex['lightBlue']):
        """
        Plot the posterior of the GP.
          - In one dimension, the function is plotted with a shaded region identifying two standard deviations.
          - In two dimsensions, a contour-plot shows the mean predicted function
          - In higher dimensions, use fixed_inputs to plot the GP  with some of the inputs fixed.

        Can plot only part of the data and part of the posterior functions
        using which_data_rowsm which_data_ycols and which_parts

        :param plot_limits: The limits of the plot. If 1D [xmin,xmax], if 2D [[xmin,ymin],[xmax,ymax]]. Defaluts to data limits
        :type plot_limits: np.array
        :param which_data_rows: which of the training data to plot (default all)
        :type which_data_rows: 'all' or a slice object to slice self.X, self.Y
        :param which_data_ycols: when the data has several columns (independant outputs), only plot these
        :type which_data_rows: 'all' or a list of integers
        :param which_parts: which of the kernel functions to plot (additively)
        :type which_parts: 'all', or list of bools
        :param fixed_inputs: a list of tuple [(i,v), (i,v)...], specifying that input index i should be set to value v.
        :type fixed_inputs: a list of tuples
        :param resolution: the number of intervals to sample the GP on. Defaults to 200 in 1D and 50 (a 50x50 grid) in 2D
        :type resolution: int
        :param levels: number of levels to plot in a contour plot.
        :type levels: int
        :param samples: the number of a posteriori samples to plot
        :type samples: int
        :param fignum: figure to plot on.
        :type fignum: figure number
        :param ax: axes to plot on.
        :type ax: axes handle
        :type output: integer (first output is 0)
        :param linecol: color of line to plot.
        :type linecol:
        :param fillcol: color of fill
        :param levels: for 2D plotting, the number of contour levels to use is ax is None, create a new figure
        """
        #deal with optional arguments
        if which_data_rows == 'all':
            which_data_rows = slice(None)
        if which_data_ycols == 'all':
            which_data_ycols = np.arange(self.output_dim)
        if len(which_data_ycols)==0:
            raise ValueError('No data selected for plotting')
        if ax is None:
            fig = pb.figure(num=fignum)
            ax = fig.add_subplot(111)

        #work out what the inputs are for plotting (1D or 2D)
        fixed_dims = np.array([i for i,v in fixed_inputs])
        free_dims = np.setdiff1d(np.arange(self.input_dim),fixed_dims)

        #one dimensional plotting
        if len(free_dims) == 1:

            #define the frame on which to plot
            resolution = resolution or 200
            Xnew, xmin, xmax = x_frame1D(self.X[:,free_dims], plot_limits=plot_limits)
            Xgrid = np.empty((Xnew.shape[0],self.input_dim))
            Xgrid[:,free_dims] = Xnew
            for i,v in fixed_inputs:
                Xgrid[:,i] = v

            #make a prediction on the frame and plot it
            if plot_raw:
                m, v = self._raw_predict(Xgrid, which_parts=which_parts)
                lower = m - 2*np.sqrt(v)
                upper = m + 2*np.sqrt(v)
                Y = self.Y
            else:
                m, v, lower, upper = self.predict(Xgrid, which_parts=which_parts)
                Y = self.Y
            for d in which_data_ycols:
                gpplot(Xnew, m[:, d], lower[:, d], upper[:, d], axes=ax, edgecol=linecol, fillcol=fillcol)
                ax.plot(self.X[which_data_rows,free_dims], Y[which_data_rows, d], 'kx', mew=1.5)

            #optionally plot some samples
            if samples: #NOTE not tested with fixed_inputs
                Ysim = self.posterior_samples(Xgrid, samples, which_parts=which_parts)
                for yi in Ysim.T:
                    ax.plot(Xnew, yi[:,None], Tango.colorsHex['darkBlue'], linewidth=0.25)
                    #ax.plot(Xnew, yi[:,None], marker='x', linestyle='--',color=Tango.colorsHex['darkBlue']) #TODO apply this line for discrete outputs.

            #set the limits of the plot to some sensible values
            ymin, ymax = min(np.append(Y[which_data_rows, which_data_ycols].flatten(), lower)), max(np.append(Y[which_data_rows, which_data_ycols].flatten(), upper))
            ymin, ymax = ymin - 0.1 * (ymax - ymin), ymax + 0.1 * (ymax - ymin)
            ax.set_xlim(xmin, xmax)
            ax.set_ylim(ymin, ymax)

        #2D plotting
        elif len(free_dims) == 2:

            #define the frame for plotting on
            resolution = resolution or 50
            Xnew, _, _, xmin, xmax = x_frame2D(self.X[:,free_dims], plot_limits, resolution)
            Xgrid = np.empty((Xnew.shape[0],self.input_dim))
            Xgrid[:,free_dims] = Xnew
            for i,v in fixed_inputs:
                Xgrid[:,i] = v
            x, y = np.linspace(xmin[0], xmax[0], resolution), np.linspace(xmin[1], xmax[1], resolution)

            #predict on the frame and plot
            if plot_raw:
                m, _ = self._raw_predict(Xgrid, which_parts=which_parts)
                Y = self.likelihood.Y
            else:
                m, _, _, _ = self.predict(Xgrid, which_parts=which_parts,sampling=False)
                Y = self.likelihood.data
            for d in which_data_ycols:
                m_d = m[:,d].reshape(resolution, resolution).T
                ax.contour(x, y, m_d, levels, vmin=m.min(), vmax=m.max(), cmap=pb.cm.jet)
                ax.scatter(self.X[which_data_rows, free_dims[0]], self.X[which_data_rows, free_dims[1]], 40, Y[which_data_rows, d], cmap=pb.cm.jet, vmin=m.min(), vmax=m.max(), linewidth=0.)

            #set the limits of the plot to some sensible values
            ax.set_xlim(xmin[0], xmax[0])
            ax.set_ylim(xmin[1], xmax[1])

            if samples:
                warnings.warn("Samples are rather difficult to plot for 2D inputs...")

        else:
            raise NotImplementedError, "Cannot define a frame with more than two input dimensions"



    def getstate(self):
        """
        Get the current state of the class, here we return everything that is needed to recompute the model.
        """
        return Model.getstate(self) + [self.X,
                self.num_data,
                self.input_dim,
                self.kern,
                self.likelihood,
                self.output_dim,
                self._Xoffset,
                self._Xscale,
                ]

    def setstate(self, state):
        self._Xscale = state.pop()
        self._Xoffset = state.pop()
        self.output_dim = state.pop()
        self.likelihood = state.pop()
        self.kern = state.pop()
        self.input_dim = state.pop()
        self.num_data = state.pop()
        self.X = state.pop()
        Model.setstate(self, state)


