"""
The :mod:`physlearn.supervised.regression` module provides a base regressor
object and the main regressor object. The latter object is designed to unify
regressors from Scikit-learn, LightGBM, XGBoost, CatBoost, and Mlxtend.
"""

# Author: Alex Wozniakowski
# License: MIT

import joblib
import re

import numpy as np
import pandas as pd

import sklearn.base
import sklearn.metrics
import sklearn.metrics._scorer
import sklearn.model_selection
import sklearn.model_selection._split
import sklearn.model_selection._validation
import sklearn.utils
import sklearn.utils.estimator_checks
import sklearn.utils.metaestimators
import sklearn.utils.multiclass
import sklearn.utils.validation

from collections import defaultdict

from physlearn.base import AdditionalRegressorMixin
from physlearn.loss import LOSS_FUNCTIONS
from physlearn.pipeline import _make_pipeline
from physlearn.supervised.interface import RegressorDictionaryInterface
from physlearn.supervised.model_selection.bayesian_search import _bayesoptcv
from physlearn.supervised.utils._data_checks import (_n_features, _n_targets,
                                                     _n_samples, _validate_data)
from physlearn.supervised.utils._definition import (_MULTI_TARGET, _REGRESSOR_DICT,
                                                    _SEARCH_METHOD, _SCORE_CHOICE)
from physlearn.supervised.utils._estimator_checks import (_check_bayesoptcv_parameter_type,
                                                          _check_estimator_choice,
                                                          _check_search_method,
                                                          _check_stacking_layer,
                                                          _preprocess_hyperparams)


class BaseRegressor(sklearn.base.BaseEstimator, sklearn.base.RegressorMixin,
                    AdditionalRegressorMixin):
    """Base class for main regressor object.
    """

    def __init__(self, regressor_choice='ridge', cv=5, random_state=0,
                 verbose=0, n_jobs=-1, score_multioutput='raw_values',
                 scoring='neg_mean_absolute_error', return_train_score=True,
                 pipeline_transform=None, pipeline_memory=None,
                 params=None, target_index=None, chain_order=None,
                 stacking_options=None, base_boosting_options=None):

        self.regressor_choice = regressor_choice
        self.cv = cv
        self.random_state = random_state
        self.verbose = verbose
        self.n_jobs = n_jobs
        self.score_multioutput = score_multioutput
        self.scoring = scoring
        self.return_train_score = return_train_score
        self.pipeline_transform = pipeline_transform
        self.pipeline_memory = pipeline_memory
        self.params = params
        self.target_index = target_index
        self.chain_order = chain_order
        self.stacking_options = stacking_options
        self.base_boosting_options = base_boosting_options
        self._validate_regressor_options()
        self._get_regressor()

    def _validate_regressor_options(self):
        self.regressor_choice = _check_estimator_choice(estimator_choice=self.regressor_choice,
                                                        estimator_type='regression')

        assert isinstance(self.cv, int) and self.cv > 1
        assert isinstance(self.random_state, int) and self.random_state >= 0
        assert isinstance(self.verbose, int) and self.verbose >= 0
        assert isinstance(self.n_jobs, int)
        assert isinstance(self.score_multioutput, str)
        assert isinstance(self.scoring, str)
        assert isinstance(self.return_train_score, bool)
        assert any(isinstance(self.pipeline_transform, built_in) for built_in in (str, list, tuple))

        if self.pipeline_memory is not None:
            assert isinstance(self.pipeline_memory, bool)

        if self.params is not None:
            assert isinstance(self.params, (dict, list))

        if self.target_index is not None:
            assert isinstance(self.target_index, int)

        if self.chain_order is not None:
            assert isinstance(self.chain_order, list)

        if self.stacking_options is not None:
            for key, option in self.stacking_options.items():
                if key == 'layers':
                    self.stacking_options[key] = _check_stacking_layer(stacking_layer=option,
                                                                       estimator_type='regression')
                elif key not in ['shuffle', 'refit', 'passthrough', 'meta_features']:
                    raise KeyError('The key: %s is not a stacking option.'
                                   % (key))

        if self.base_boosting_options is not None:
            # The options are checked in the
            # ModifiedPipeline constructor.
            assert isinstance(self.base_boosting_options, dict)

    def _get_regressor(self):
        reg = RegressorDictionaryInterface(regressor_choice=self.regressor_choice,
                                           params=self.params,
                                           stacking_options=self.stacking_options)

        kwargs = dict(cv=self.cv,
                      verbose=self.verbose,
                      random_state=self.random_state,
                      n_jobs=self.n_jobs,
                      stacking_options=self.stacking_options)

        # The (hyper)parameters must be set
        # before retrieval.
        self._regressor = reg.set_params(**kwargs)
        self.params = reg.get_params(regressor=self._regressor)

    @property
    def check_regressor(self):
        """Check if regressor adheres to scikit-learn conventions."""

        return sklearn.utils.estimator_checks.check_estimator(self._regressor)

    def get_params(self, deep=True):
        """Retrieve parameters."""

        # Override method in BaseEstimator
        return self.params

    def set_params(self, **params):
        """Set parameters of regressor choice."""

        if not params:
            # Simple optimization to gain speed (inspect is slow)
            return self
        valid_params = self.get_params(deep=True)

        nested_params = defaultdict(dict)  # grouped by prefix
        for key, value in params.items():
            key, delim, sub_key = key.partition('__')
            if key not in valid_params:
                raise ValueError('Invalid parameter %s for regressor %s. '
                                 'Check the list of available parameters '
                                 'with `regressor.get_params().keys()`.'
                                 % (key, self))

            if delim:
                nested_params[key][sub_key] = value
            else:
                setattr(self._regressor, key, value)
                valid_params[key] = value

        for key, sub_params in nested_params.items():
            valid_params[key].set_params(**sub_params)

        return self

    def _validate_data(self, X=None, y=None):
        """Checks the validity of the data representation(s)."""

        if X is not None and y is not None:
            if not hasattr(self, '_validated_data'):
                out = _validate_data(X=X, y=y)
                setattr(self, '_validated_data', True)
            else:
                out = X, y
        elif X is not None:
            if not hasattr(self, '_validated_data'):
                out = _validate_data(X=X)
            else:
                out = X
        elif y is not None:
            if not hasattr(self, '_validated_data'):
                out = _validate_data(y=y)
            else:
                out = y
        else:
            raise ValueError('Both the data matrix X and the target matrix y are None. '
                             'Thus, there is no data to validate.')

        return out


    def dump(self, value, filename):
        """Save a file in joblib format."""

        assert isinstance(filename, str)
        joblib.dump(value=value, filename=filename)

    def load(self, filename):
        """Load a file in joblib format."""

        assert isinstance(filename, str)        
        return joblib.load(filename=filename)
    
    def get_pipeline(self, y, n_quantiles=None):
        """Create pipe attribute for downstream tasks."""

        y = self._validate_data(y=y)

        if n_quantiles is None and isinstance(self.pipeline_transform, str):
            if re.search('quantile', self.pipeline_transform):
                n_quantiles = _n_samples(y)

        kwargs = dict(random_state=self.random_state,
                      verbose=self.verbose,
                      n_jobs=self.n_jobs,
                      cv=self.cv,
                      memory=self.pipeline_memory,
                      target_index=self.target_index,
                      target_type = sklearn.utils.multiclass.type_of_target(y),
                      n_quantiles=n_quantiles,
                      chain_order=self.chain_order,
                      base_boosting_options=self.base_boosting_options)

        self.pipe =  _make_pipeline(estimator=self._regressor,
                                    transform=self.pipeline_transform,
                                    **kwargs)

    def regattr(self, attr):
        """Get regressor attribute from pipeline."""

        assert hasattr(self, 'pipe') and isinstance(attr, str)

        try:
            attr = {f'target {index}': getattr(self.pipe, attr)
                   for index, self.pipe
                   in enumerate(self.pipe.named_steps['reg'].estimators_)}
            return attr
        except:
            raise AttributeError('%s needs to have an estimators_ attribute '
                                 'in order to access the attribute: %s.'
                                 % (self.pipe.named_steps['reg'], attr))

    def _check_target_index(self, y):
        """Automates single-target regression subtask slicing."""

        y = self._validate_data(y=y)

        if self.target_index is not None and \
        sklearn.utils.multiclass.type_of_target(y) in _MULTI_TARGET:
            # Selects a particular single-target
            return y.iloc[:, self.target_index]
        else:
            return y

    @staticmethod
    def _fit(regressor, X, y, sample_weight=None):
        """Helper fit method."""

        if sample_weight is not None:
            try:
                regressor.fit(X=X, y=y, sample_weight=sample_weight)
            except TypeError as exc:
                if 'unexpected keyword argument sample_weight' in str(exc):
                    raise TypeError('%s does not support sample weights.'
                                    % (regressor.__class__.__name__)) from exc
        else:
            regressor.fit(X=X, y=y)

    def fit(self, X, y, sample_weight=None):
        """Fit regressor."""

        X, y = self._validate_data(X=X, y=y)

        # Automates single-target slicing.
        y = self._check_target_index(y=y)

        if not hasattr(self, 'pipe'):
            self.get_pipeline(y=y)

        self._fit(regressor=self.pipe, X=X, y=y,
                  sample_weight=sample_weight)

        return self.pipe

    def predict(self, X):
        """Generate predictions."""

        assert hasattr(self, 'pipe')
        X = self._validate_data(X=X)

        return self.pipe.predict(X=X)

    def score(self, y_true, y_pred, scoring, multioutput):
        """Compute score in supervised fashion."""

        assert any(scoring for method in _SCORE_CHOICE) and isinstance(scoring, str)

        if scoring in ['r2', 'ev']:
            possible_multioutputs = ['raw_values', 'uniform_average',
                                     'variance_weighted']
            assert any(multioutput for output in possible_multioutputs)
        else:
            possible_multioutputs = ['raw_values', 'uniform_average']
            assert any(multioutput for output in possible_multioutputs)

        # Automates single-target slicing
        y_true = self._check_target_index(y=y_true)

        if scoring == 'mae':
            score = sklearn.metrics.mean_absolute_error(y_true=y_true, y_pred=y_pred,
                                                        multioutput=multioutput)
        elif scoring == 'mse':
            score = sklearn.metrics.mean_squared_error(y_true=y_true, y_pred=y_pred,
                                                       multioutput=multioutput)
        elif scoring == 'rmse':
            score = np.sqrt(sklearn.metrics.mean_squared_error(y_true=y_true, y_pred=y_pred,
                                                               multioutput=multioutput))
        elif scoring == 'r2':
            score = sklearn.metrics.r2_score(y_true=y_true, y_pred=y_pred,
                                             multioutput=multioutput)
        elif scoring == 'ev':
            score = sklearn.metrics.explained_variance_score(y_true=y_true, y_pred=y_pred,
                                                             multioutput=multioutput)
        elif scoring == 'msle':
            try:
                score = sklearn.metrics.mean_squared_log_error(y_true=y_true, y_pred=y_pred,
                                                               multioutput=multioutput)
            except:
                # Sklearn will raise a ValueError if either
                # statement is true, so we circumvent
                # this error and score with a NaN.
                score = np.nan

        return score

    def _modified_cross_validate(self, X, y, return_regressor=False, error_score=np.nan,
                                 return_incumbent_score=False, cv=None):
        """
        Perform cross-validation for regressor and incumbent,
        if return_incumbent_score is True.
        """

        X, y = self._validate_data(X=X, y=y)

        # Automates single-target slicing.
        y = self._check_target_index(y=y)

        X, y, groups = sklearn.utils.validation.indexable(X, y, None)

        if cv is None:
            cv = self.cv

        if not hasattr(self, 'pipe'):
            n_samples = _n_samples(y)
            if isinstance(cv, int):
                fold_size =  np.full(shape=n_samples, fill_value=n_samples // cv,
                                     dtype=np.int)
            else:
                fold_size =  np.full(shape=n_samples, fill_value=n_samples // cv.n_splits,
                                     dtype=np.int)
            estimate_fold_size = n_samples - (np.max(fold_size) + 1)
            self.get_pipeline(y=y, n_quantiles=estimate_fold_size)

        cv = sklearn.model_selection._split.check_cv(cv=cv, y=y, classifier=False)

        scorers, _ = sklearn.metrics._scorer._check_multimetric_scoring(estimator=self.pipe,
                                                                        scoring=self.scoring)

        parallel = joblib.Parallel(n_jobs=self.n_jobs, verbose=self.verbose,
                                   pre_dispatch='2*n_jobs')

        scores = parallel(
            joblib.delayed(sklearn.model_selection._validation._fit_and_score)(
                estimator=sklearn.base.clone(self.pipe), X=X, y=y, scorer=scorers, train=train,
                test=test, verbose=self.verbose, parameters=None, fit_params=None,
                return_train_score=self.return_train_score, return_parameters=False,
                return_n_test_samples=False, return_times=True,
                return_estimator=return_regressor, error_score=np.nan)
            for train, test in cv.split(X, y, groups))

        if return_incumbent_score:
            if self.target_index is not None:
                y_pred = X.iloc[:, self.target_index]
            else:
                y_pred = X

            incumbent_test_score = parallel(
                joblib.delayed(self.score)(
                    y_true=y.loc[test], y_pred=y_pred.loc[test])
                for _, test in cv.split(X, y, groups))

            if self.scoring == 'neg_mean_absolute_error':
                incumbent_test_score = [score['mae'].values[0] for score in incumbent_test_score]
            elif self.scoring == 'neg_mean_squared_error':
                incumbent_test_score = [score['mse'].values[0] for score in incumbent_test_score]

        zipped_scores = list(zip(*scores))
        if self.return_train_score:
            train_scores = zipped_scores.pop(0)
            train_scores = sklearn.model_selection._validation._aggregate_score_dicts(train_scores)
        if return_regressor:
            fitted_regressors = zipped_scores.pop()
        test_scores, fit_times, score_times = zipped_scores
        test_scores = sklearn.model_selection._validation._aggregate_score_dicts(test_scores)

        ret = {}
        ret['fit_time'] = np.array(fit_times)
        ret['score_time'] = np.array(score_times)

        if return_regressor:
            ret['regressor'] = fitted_regressors

        for name in scorers:
            ret['test_%s' % name] = np.array(test_scores[name])
            if self.return_train_score:
                key = 'train_%s' % name
                ret[key] = np.array(train_scores[name])

        if return_incumbent_score:
            ret['incumbent_test_score'] = incumbent_test_score

        return ret

    def cross_validate(self, X, y, return_incumbent_score=False, cv=None):
        """
        Retrieve cross-validation results for regressor and incumbent,
        if return_incumbent_score is True.
        """

        scores_dict = self._modified_cross_validate(X=X, y=y,
                                                    return_incumbent_score=return_incumbent_score,
                                                    cv=cv)

        # Sklearn returns negative MAE and MSE scores,
        # so we restore nonnegativity.
        if self.scoring in ['neg_mean_absolute_error', 'neg_mean_squared_error']:
            scores_dict['train_score'] = np.array([np.abs(score) for score in scores_dict['train_score']])
            scores_dict['test_score'] = np.array([np.abs(score) for score in scores_dict['test_score']])

        return pd.DataFrame(scores_dict)

    def cross_val_score(self, X, y, return_incumbent_score=False, cv=None):
        """
        Retrieve withheld fold errors for regressor and incumbent,
        if return_incumbent_score is True.
        """

        scores_dict = self.cross_validate(X=X, y=y,
                                          return_incumbent_score=return_incumbent_score,
                                          cv=cv)

        if return_incumbent_score:
            return scores_dict[['test_score', 'incumbent_test_score']]
        else:
            return scores_dict['test_score']


class Regressor(BaseRegressor):
    """
    Main regressor class for building a prediction model.

    Important methods are fit, baseboostcv, predict, search, and nested_cross_validate.
    """

    def __init__(self, regressor_choice='ridge', cv=5, random_state=0,
                 verbose=1, n_jobs=-1, score_multioutput='raw_values',
                 scoring='neg_mean_absolute_error', refit=True,
                 randomizedcv_n_iter=20, bayesoptcv_init_points=2,
                 bayesoptcv_n_iter=20, return_train_score=True,
                 pipeline_transform='quantilenormal', pipeline_memory=None,
                 params=None, target_index=None, chain_order=None,
                 stacking_options=None, base_boosting_options=None):

        super().__init__(regressor_choice=regressor_choice,
                         cv=cv,
                         random_state=random_state,
                         verbose=verbose,
                         n_jobs=n_jobs,
                         score_multioutput=score_multioutput,
                         scoring=scoring,
                         return_train_score=return_train_score,
                         pipeline_transform=pipeline_transform,
                         pipeline_memory=pipeline_memory,
                         params=params,
                         target_index=target_index,
                         chain_order=chain_order,
                         stacking_options=stacking_options,
                         base_boosting_options=base_boosting_options)

        self.refit = refit
        self.randomizedcv_n_iter = randomizedcv_n_iter
        self.bayesoptcv_init_points = bayesoptcv_init_points
        self.bayesoptcv_n_iter = bayesoptcv_n_iter
        self._validate_search_options()

    def _validate_search_options(self):
        assert isinstance(self.refit, bool)
        assert isinstance(self.randomizedcv_n_iter, int)
        assert isinstance(self.bayesoptcv_init_points, int)
        assert isinstance(self.bayesoptcv_n_iter, int)

    @property
    def check_regressor(self):
        """Check if the regressor adheres to the Scikit-learn estimator convention."""

        # Sklearn and Mlxtend stacking regressors, as well as 
        # LightGBM, XGBoost, and CatBoost regressor do not
        # adhere to the convention.
        try:
            super().check_regressor
        except:
            raise TypeError('%s does not adhere to the Scikit-learn estimator convention.'
                            % (_REGRESSOR_DICT[self.regressor_choice]))

    def get_params(self, deep=True):
        """Retrieve parameters."""

        return super().get_params(deep=deep)

    def set_params(self, **params):
        """Set parameters of regressor choice."""

        return super().set_params(**params)

    def dump(self, value, filename):
        """Save a file in joblib format."""

        super().dump(value=value, filename=filename)

    def load(self, filename):
        """Load a file in joblib format."""

        return super().load(filename=filename)

    def regattr(self, attr):
        """Gets the regressor's attribute from the pipeline."""

        return super().regattr(attr=attr)

    def fit(self, X, y, sample_weight=None):
        """Fits a regressor."""

        return super().fit(X=X, y=y, sample_weight=sample_weight)

    def _inbuilt_model_selection_step(self, X, y):
        """Cross-validates the incumbent and the candidate regressor."""

        cross_val_score = super().cross_val_score(X=X, y=y,
                                                  return_incumbent_score=True)
        mean_cross_val_score = cross_val_score.mean(axis=0)

        if mean_cross_val_score[0] >= mean_cross_val_score[1]:
            # Base boosting did not improve performance
            setattr(self, '_return_incumbent', True)

    def baseboostcv(self, X, y, sample_weight=None):
        """Base boosting with inbuilt cross-validation."""

        X, y = super()._validate_data(X=X, y=y)

        # Automates single-target slicing
        y = super()._check_target_index(y=y)

        # Performs augmented k-fold cross-validation, then it
        # selects either the incumbent or the candidate.
        self._inbuilt_model_selection_step(X=X, y=y)

        if not hasattr(self, 'pipe'):
            super().get_pipeline(y=y)

        if not hasattr(self, '_return_incumbent'):
            # This checks if the candidate was chosen
            # in model selection.
            super().fit(X=X, y=y, sample_weight=sample_weight)
            return self.pipe
        else:
            setattr(self, 'return_incumbent_', True) 
            return self

    def predict(self, X):
        """Generates predictions."""

        X = self._validate_data(X=X)

        if hasattr(self, 'return_incumbent_'):
            # This checks if the incumbent was chosen in
            # the inbuilt model selection step in the
            # cross-validated version of base boosting.
            if self.target_index is not None:
                y_pred = X.iloc[:, self.target_index]
            else:
                y_pred = X
        else:
            assert hasattr(self, 'pipe')
            y_pred = self.pipe.predict(X=X)

        return y_pred

    def score(self, y_true, y_pred, path=None):
        """Computes the DataFrame of scores."""

        score_summary = {}
        for scoring in _SCORE_CHOICE:
            score_summary[scoring] = super().score(y_true=y_true, y_pred=y_pred,
                                                   scoring=scoring,
                                                   multioutput=self.score_multioutput)

        score_summary_df = pd.DataFrame(score_summary).dropna(how='any', axis=1)
        score_summary_df.index.name = 'target'
        
        # Shifts the index origin by one.
        if self.target_index is not None:
            score_summary_df.index = pd.RangeIndex(start=self.target_index + 1,
                                                   stop=self.target_index + 2,
                                                   step=1)

        if path is not None:
            assert isinstance(path, str)
            score_summary_df.to_csv(path_or_buf=path)

        return score_summary_df

    def cross_validate(self, X, y, return_incumbent_score=False, cv=None):
        """Retrieves the cross-validation results."""

        return super().cross_validate(X=X, y=y,
                                      return_incumbent_score=return_incumbent_score,
                                      cv=cv)

    def cross_val_score(self, X, y, return_incumbent_score=False, cv=None):
        """Retrieves the cross-validation score."""

        return super().cross_val_score(X=X, y=y,
                                       return_incumbent_score=return_incumbent_score,
                                       cv=cv)

    def _preprocess_search_params(self, y, search_params):
        """Helper method preprocesses the (hyper)parameters.

        Preprocesses the (hyper)parameter names for exhaustive search.
        This requires checking whether the task is single-target or
        multi-target regression. If the task is multi-target regression,
        then this further requires checking whether the single-target
        regression subtasks are assumed to be independent or chained. 
        """

        if sklearn.utils.multiclass.type_of_target(y) in _MULTI_TARGET:
            if self.chain_order is not None:
                search_params = _preprocess_hyperparams(raw_params=search_params,
                                                        multi_target=True,
                                                        chain=True)
            else:
                search_params = _preprocess_hyperparams(raw_params=search_params,
                                                        multi_target=True,
                                                        chain=False)
        else:
            search_params = _preprocess_hyperparams(raw_params=search_params,
                                                    multi_target=False,
                                                    chain=False)

        return search_params

    def _estimate_fold_size(self, y, cv):
        """Helper method to estimate cross-validation fold size."""

        n_samples = _n_samples(y)
        if isinstance(cv, int):
            fold_size =  np.full(shape=n_samples, fill_value=n_samples // cv,
                                 dtype=np.int)
        else:
            fold_size =  np.full(shape=n_samples, fill_value=n_samples // cv.n_splits,
                                 dtype=np.int)
        return n_samples - (np.max(fold_size) + 1)

    def _search(self, X, y, search_params, search_method='gridsearchcv', cv=None):
        """Helper (hyper)parameter search method."""

        # The returned search method is either sequential
        # or parallell. The former method identifies Bayesian
        # optimization, while the latter method identifies
        # grid or randomized search.
        search_method = _check_search_method(search_method=search_method)
        search_params = self._preprocess_search_params(y=y, search_params=search_params)
        if cv is None:
            cv = self.cv

        if not hasattr(self, 'pipe'):
            self.get_pipeline(y=y,
                              n_quantiles=self._estimate_fold_size(y=y, cv=cv))

        if search_method == 'gridsearchcv':
            self._regressor_search = sklearn.model_selection.GridSearchCV(
                estimator=self.pipe, param_grid=search_params,
                scoring=self.scoring, refit=self.refit, n_jobs=self.n_jobs,
                cv=cv, verbose=self.verbose, pre_dispatch='2*n_jobs',
                error_score=np.nan, return_train_score=self.return_train_score)
        elif search_method == 'randomizedsearchcv':
            self._regressor_search = sklearn.model_selection.RandomizedSearchCV(
                estimator=self.pipe, param_distributions=search_params,
                n_iter=self.randomizedcv_n_iter, scoring=self.scoring,
                n_jobs=self.n_jobs, refit=self.refit, cv=cv,
                verbose=self.verbose, pre_dispatch='2*n_jobs',
                error_score=np.nan, return_train_score=self.return_train_score)
        elif search_method == 'bayesoptcv':
            self.optimization = _bayesoptcv(X=X, y=y, estimator=self.pipe,
                                            search_params=search_params,
                                            cv=cv, scoring=self.scoring,
                                            n_jobs=self.n_jobs,
                                            verbose=self.verbose,
                                            random_state=self.random_state,
                                            init_points=self.bayesoptcv_init_points,
                                            n_iter=self.bayesoptcv_n_iter)

            if self.refit:
                max_params = self.optimization.max['params']
                get_best_params_ = _check_bayesoptcv_parameter_type(max_params)
                self._regressor_search = self.pipe.set_params(**get_best_params_)

    def search(self, X, y, search_params, search_method='gridsearchcv',
               cv=None, path=None):
        """(Hyper)parameter search method."""

        X, y = super()._validate_data(X=X, y=y)

        # Automates single-target slicing.
        y = self._check_target_index(y=y)

        self._search(X=X, y=y, search_params=search_params,
                     search_method=search_method, cv=cv)

        try:
            self._regressor_search.fit(X=X, y=y)
        except:
            raise AttributeError('Performing the search requires the '
                                 'attribute: %s. However, the attribute '
                                 'is not set.'
                                 % (_regressor_search))

        if search_method in ['gridsearchcv', 'randomizedsearchcv']:
            self.best_params_ = pd.Series(self._regressor_search.best_params_)
            self.best_score_ = pd.Series({'best_score': self._regressor_search.best_score_})
        elif search_method == 'bayesoptcv':
            try:
                self.best_params_ = pd.Series(self.optimization.max['params'])
                self.best_score_ = pd.Series({'best_score': self.optimization.max['target']})
            except:
                raise AttributeError('In order to set the attributes: %s and %s, '
                                     'there must be the attribute: %s.'
                                     % (best_params_, best_score_, optimization))

        # Sklearn and bayes-opt return negative
        # MAE and MSE scores, so we restore
        # nonnegativity.
        if re.match('neg', self.scoring):
            self.best_score_.loc['best_score'] *= -1.0

        self.search_summary_ = pd.concat([self.best_score_, self.best_params_], axis=0)

        # Filter based on sklearn model search attributes.
        _sklearn_list = ['best_estimator_', 'cv_results_', 'refit_time_']
        if all(hasattr(self._regressor_search, attr) for attr in _sklearn_list):
            self.pipe = self._regressor_search.best_estimator_
            self.best_regressor_ = self._regressor_search.best_estimator_
            self.pipe = self._regressor_search.best_estimator_
            self.cv_results_ = pd.DataFrame(self._regressor_search.cv_results_)
            self.refit_time_ = pd.Series({'refit_time':self._regressor_search.refit_time_})
            self.search_summary_ = pd.concat([self.search_summary_, self.refit_time_], axis=0)

        if path is not None:
            assert isinstance(path, str)
            self.search_summary_.to_csv(path_or_buf=path, header=True)

    def _search_and_score(self, estimator, X, y, scorer, train, test, verbose,
                          search_params, search_method='gridsearchcv', cv=None):
        """Helper method for nested cross-validation.

        Exhaustively searches over the specified (hyper)parameters in the inner
        loop then scores the best performing regressor in the outer loop.
        """

        X_train, y_train = sklearn.utils.metaestimators._safe_split(estimator=estimator,
                                                                    X=X, y=y,
                                                                    indices=train)
        X_test, y_test = sklearn.utils.metaestimators._safe_split(estimator=estimator,
                                                                  X=X, y=y,
                                                                  indices=test,
                                                                  train_indices=train)

        self.search(X=X_train, y=y_train, search_params=search_params,
                    search_method=search_method, cv=cv)

        if not self.refit:
            self.pipe = sklearn.base.clone(sklearn.base.clone(self.pipe).set_params(
                **self.best_params_))
            self.pipe._fit(X=X_train, y=y_train)

        test_score = sklearn.model_selection._validation._score(estimator=self.pipe,
                                                                X_test=X_test,
                                                                y_test=y_test,
                                                                scorer=scorer)

        return (self.best_score_.values, test_score)

    def nested_cross_validate(self, X, y, search_params, search_method='gridsearchcv', outer_cv=None,
                              inner_cv=None, return_inner_loop_score=False):
        """Performs a nested cross-validation procedure.

        Notes
        -----
        The procedure does not compute the single best set of (hyper)parameters, as each inner
        loop may return a different set of optimal (hyper)parameters.

        References
        ----------
        Jacques Wainer and Gavin Cawley. "Nested cross-validation when selecting
        classifiers is overzealous for most practical applications," arXiv preprint
        arXiv:1809.09446 (2018).
        """

        X, y = super()._validate_data(X=X, y=y)

        # Automates single-target slicing
        y = self._check_target_index(y=y)

        X, y, groups = sklearn.utils.validation.indexable(X, y, None)

        if outer_cv is None:
            outer_cv = self.cv

        if inner_cv is None:
            inner_cv = self.cv

        if not hasattr(self, 'pipe'):
            self.get_pipeline(y=y,
                              n_quantiles=self._estimate_fold_size(y=y, cv=outer_cv))

        outer_cv = sklearn.model_selection._split.check_cv(cv=outer_cv, y=y,
                                                           classifier=False)

        scorers, _ = sklearn.metrics._scorer._check_multimetric_scoring(estimator=self.pipe,
                                                                        scoring=self.scoring)

        parallel = joblib.Parallel(n_jobs=self.n_jobs, verbose=self.verbose,
                                   pre_dispatch='2*n_jobs')

        # Parallelized nested cross-validation: the helper method utilizes
        # the search method to select a regressor from the inner loop, then
        # the performance of this regressor is evaluated in the outer loop.
        scores = parallel(
            joblib.delayed(self._search_and_score)(
                estimator=sklearn.base.clone(self.pipe), X=X, y=y, scorer=scorers,
                train=train, test=test, verbose=self.verbose, search_params=search_params,
                search_method='gridsearchcv', cv=inner_cv)
            for train, test in outer_cv.split(X, y, groups))

        # Sklearn and bayes-opt return negative MAE and MSE scores,
        # so we restore nonnegativity.
        outer_loop_scores = pd.Series([np.abs(pair[1]['score']) for pair in scores])

        if return_inner_loop_score:
            inner_loop_scores = pd.Series(np.concatenate([np.abs(pair[0]) for pair in scores]))
            return outer_loop_scores, inner_loop_scores
        else:
            return outer_loop_scores
        
    def subsample(self, X, y, subsample_proportion=None):
        """Generate subsamples from data X, y."""

        if subsample_proportion is not None:
            assert subsample_proportion > 0 and subsample_proportion < 1
            n_samples = int(len(X) * subsample_proportion)
            X, y = sklearn.utils.resample(X, y, replace=False,
                                          n_samples=n_samples,
                                          random_state=self.random_state)
        else:
            X, y = sklearn.utils.resample(X, y, replace=False,
                                          n_samples=len(X),
                                          random_state=self.random_state)

        return X, y
