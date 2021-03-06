"""
Copyright (c) 2019 Intel Corporation

Licensed under the Apache License, Version 2.0 (the "License");
you may not use this file except in compliance with the License.
You may obtain a copy of the License at

      http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing, software
distributed under the License is distributed on an "AS IS" BASIS,
WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
See the License for the specific language governing permissions and
limitations under the License.
"""

import warnings
import math
from functools import singledispatch
import numpy as np

from ..representation import (
    RegressionAnnotation,
    RegressionPrediction,
    FacialLandmarksAnnotation,
    FacialLandmarksPrediction,
    SuperResolutionAnnotation,
    SuperResolutionPrediction,
    GazeVectorAnnotation,
    GazeVectorPrediction,
    DepthEstimationAnnotation,
    DepthEstimationPrediction,
    ImageInpaintingAnnotation,
    ImageInpaintingPrediction
)

from .metric import PerImageEvaluationMetric
from ..config import BaseField, NumberField, BoolField, ConfigError, StringField
from ..utils import string_to_tuple, finalize_metric_result


class BaseRegressionMetric(PerImageEvaluationMetric):
    annotation_types = (RegressionAnnotation, DepthEstimationAnnotation)
    prediction_types = (RegressionPrediction, DepthEstimationPrediction)

    def __init__(self, value_differ, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.value_differ = value_differ
        self.calculate_diff = singledispatch(self._calculate_diff_regression_rep)
        self.calculate_diff.register(DepthEstimationAnnotation, self._calculate_diff_depth_estimation_rep)

    def configure(self):
        self.meta.update({
            'names': ['mean', 'std'], 'scale': 1, 'postfix': ' ', 'calculate_mean': False, 'target': 'higher-worse'
        })
        self.magnitude = []

    def update(self, annotation, prediction):
        diff = self.calculate_diff(annotation, prediction)
        self.magnitude.append(diff)

        return diff

    def _calculate_diff_regression_rep(self, annotation, prediction):
        return self.value_differ(annotation.value, prediction.value)

    def _calculate_diff_depth_estimation_rep(self, annotation, prediction):
        diff = annotation.mask * self.value_differ(annotation.depth_map, prediction.depth_map)
        ret = 0

        if np.sum(annotation.mask) > 0:
            ret = np.sum(diff) / np.sum(annotation.mask)

        return ret

    def evaluate(self, annotations, predictions):
        return np.mean(self.magnitude), np.std(self.magnitude)

    def reset(self):
        self.magnitude = []


class BaseRegressionOnIntervals(PerImageEvaluationMetric):
    annotation_types = (RegressionAnnotation, )
    prediction_types = (RegressionPrediction, )

    @classmethod
    def parameters(cls):
        parameters = super().parameters()
        parameters.update({
            'intervals': BaseField(optional=True, description="Comma-separated list of interval boundaries."),
            'start': NumberField(
                optional=True, default=0.0,
                description="Start value: way to generate range of intervals from start to end with length step."),
            'end': NumberField(
                optional=True,
                description="Stop value: way to generate range of intervals from start to end with length step."
            ),
            'step': NumberField(
                optional=True, default=1.0,
                description="Step value: way to generate range of intervals from start to end with length step."
            ),
            'ignore_values_not_in_interval': BoolField(
                optional=True, default=True,
                description="Allows create additional intervals for values less than minimal value "
                            "in interval and greater than maximal."
            )
        })

        return parameters

    def __init__(self, value_differ, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.value_differ = value_differ

    def configure(self):
        self.meta.update({'scale': 1, 'postfix': ' ', 'calculate_mean': False, 'target': 'higher-worse'})
        self.ignore_out_of_range = self.get_value_from_config('ignore_values_not_in_interval')

        self.intervals = self.get_value_from_config('intervals')
        if not self.intervals:
            stop = self.get_value_from_config('end')
            if not stop:
                raise ConfigError('intervals or start-step-end of interval should be specified for metric')

            start = self.get_value_from_config('start')
            step = self.get_value_from_config('step')
            self.intervals = np.arange(start, stop + step, step)

        if not isinstance(self.intervals, (list, np.ndarray)):
            self.intervals = string_to_tuple(self.intervals)

        self.intervals = np.unique(self.intervals)
        self.magnitude = [[] for _ in range(len(self.intervals) + 1)]
        self._create_meta()

    def update(self, annotation, prediction):
        index = find_interval(annotation.value, self.intervals)
        diff = self.value_differ(annotation.value, prediction.value)
        self.magnitude[index].append(diff)

        return diff

    def evaluate(self, annotations, predictions):
        if self.ignore_out_of_range:
            self.magnitude = self.magnitude[1:-1]

        result = [[np.mean(values), np.std(values)] if values else [np.nan, np.nan] for values in self.magnitude]
        result, self.meta['names'] = finalize_metric_result(np.reshape(result, -1), self.meta['names'])

        if not result:
            warnings.warn("No values in given interval")
            result.append(0)

        return result

    def _create_meta(self):
        self.meta['names'] = ([])
        if not self.ignore_out_of_range:
            self.meta['names'] = (['mean: < ' + str(self.intervals[0]), 'std: < ' + str(self.intervals[0])])

        for index in range(len(self.intervals) - 1):
            self.meta['names'].append('mean: <= ' + str(self.intervals[index]) + ' < ' + str(self.intervals[index + 1]))
            self.meta['names'].append('std: <= ' + str(self.intervals[index]) + ' < ' + str(self.intervals[index + 1]))

        if not self.ignore_out_of_range:
            self.meta['names'].append('mean: > ' + str(self.intervals[-1]))
            self.meta['names'].append('std: > ' + str(self.intervals[-1]))

    def reset(self):
        self.magnitude = [[] for _ in range(len(self.intervals) + 1)]
        self._create_meta()


class MeanAbsoluteError(BaseRegressionMetric):
    __provider__ = 'mae'

    def __init__(self, *args, **kwargs):
        super().__init__(mae_differ, *args, **kwargs)


class MeanSquaredError(BaseRegressionMetric):
    __provider__ = 'mse'

    def __init__(self, *args, **kwargs):
        super().__init__(mse_differ, *args, **kwargs)


class RootMeanSquaredError(BaseRegressionMetric):
    __provider__ = 'rmse'

    def __init__(self, *args, **kwargs):
        super().__init__(mse_differ, *args, **kwargs)

    def update(self, annotation, prediction):
        rmse = np.sqrt(self.calculate_diff(annotation, prediction))
        self.magnitude.append(rmse)
        return rmse

    def evaluate(self, annotations, predictions):
        return np.mean(self.magnitude), np.std(self.magnitude)


class MeanAbsoluteErrorOnInterval(BaseRegressionOnIntervals):
    __provider__ = 'mae_on_interval'

    def __init__(self, *args, **kwargs):
        super().__init__(mae_differ, *args, **kwargs)


class MeanSquaredErrorOnInterval(BaseRegressionOnIntervals):
    __provider__ = 'mse_on_interval'

    def __init__(self, *args, **kwargs):
        super().__init__(mse_differ, *args, **kwargs)


class RootMeanSquaredErrorOnInterval(BaseRegressionOnIntervals):
    __provider__ = 'rmse_on_interval'

    def __init__(self, *args, **kwargs):
        super().__init__(mse_differ, *args, **kwargs)

    def update(self, annotation, prediction):
        mse = super().update(annotation, prediction)
        return np.sqrt(mse)

    def evaluate(self, annotations, predictions):
        if self.ignore_out_of_range:
            self.magnitude = self.magnitude[1:-1]

        result = []
        for values in self.magnitude:
            error = [np.sqrt(np.mean(values)), np.sqrt(np.std(values))] if values else [np.nan, np.nan]
            result.append(error)

        result, self.meta['names'] = finalize_metric_result(np.reshape(result, -1), self.meta['names'])

        if not result:
            warnings.warn("No values in given interval")
            result.append(0)

        return result


class FacialLandmarksPerPointNormedError(PerImageEvaluationMetric):
    __provider__ = 'per_point_normed_error'

    annotation_types = (FacialLandmarksAnnotation, )
    prediction_types = (FacialLandmarksPrediction, )

    def configure(self):
        self.meta.update({
            'scale': 1, 'postfix': ' ', 'calculate_mean': True, 'data_format': '{:.4f}', 'target': 'higher-worse'
        })
        self.magnitude = []

    def update(self, annotation, prediction):
        result = point_regression_differ(
            annotation.x_values, annotation.y_values, prediction.x_values, prediction.y_values
        )
        result /= np.maximum(annotation.interocular_distance, np.finfo(np.float64).eps)
        self.magnitude.append(result)

        return result

    def evaluate(self, annotations, predictions):
        num_points = np.shape(self.magnitude)[1]
        point_result_name_pattern = 'point_{}_normed_error'
        self.meta['names'] = [point_result_name_pattern.format(point_id) for point_id in range(num_points)]
        per_point_rmse = np.mean(self.magnitude, axis=0)
        per_point_rmse, self.meta['names'] = finalize_metric_result(per_point_rmse, self.meta['names'])

        return per_point_rmse

    def reset(self):
        self.magnitude = []


class FacialLandmarksNormedError(PerImageEvaluationMetric):
    __provider__ = 'normed_error'

    annotation_types = (FacialLandmarksAnnotation, )
    prediction_types = (FacialLandmarksPrediction, )

    @classmethod
    def parameters(cls):
        parameters = super().parameters()
        parameters.update({
            'calculate_std': BoolField(
                optional=True, default=False, description="Allows calculation of standard deviation"
            ),
            'percentile': NumberField(
                optional=True, value_type=int, min_value=0, max_value=100,
                description="Calculate error rate for given percentile."
            )
        })

        return parameters

    def configure(self):
        self.calculate_std = self.get_value_from_config('calculate_std')
        self.percentile = self.get_value_from_config('percentile')
        self.meta.update({
            'scale': 1,
            'postfix': ' ',
            'calculate_mean': not self.calculate_std or not self.percentile,
            'data_format': '{:.4f}',
            'target': 'higher-worse'
        })
        self.magnitude = []

    def update(self, annotation, prediction):
        per_point_result = point_regression_differ(
            annotation.x_values, annotation.y_values, prediction.x_values, prediction.y_values
        )
        avg_result = np.sum(per_point_result) / len(per_point_result)
        avg_result /= np.maximum(annotation.interocular_distance, np.finfo(np.float64).eps)
        self.magnitude.append(avg_result)

        return avg_result

    def evaluate(self, annotations, predictions):
        self.meta['names'] = ['mean']
        result = [np.mean(self.magnitude)]

        if self.calculate_std:
            result.append(np.std(self.magnitude))
            self.meta['names'].append('std')

        if self.percentile:
            sorted_magnitude = np.sort(self.magnitude)
            index = len(self.magnitude) / 100 * self.percentile
            result.append(sorted_magnitude[int(index)])
            self.meta['names'].append('{}th percentile'.format(self.percentile))

        return result

    def reset(self):
        self.magnitude = []


def calculate_distance(x_coords, y_coords, selected_points):
    first_point = [x_coords[selected_points[0]], y_coords[selected_points[0]]]
    second_point = [x_coords[selected_points[1]], y_coords[selected_points[1]]]
    return np.linalg.norm(np.subtract(first_point, second_point))


def mae_differ(annotation_val, prediction_val):
    return np.abs(annotation_val - prediction_val)


def mse_differ(annotation_val, prediction_val):
    return (annotation_val - prediction_val)**2


def find_interval(value, intervals):
    for index, point in enumerate(intervals):
        if value < point:
            return index

    return len(intervals)


def point_regression_differ(annotation_val_x, annotation_val_y, prediction_val_x, prediction_val_y):
    loss = np.subtract(list(zip(annotation_val_x, annotation_val_y)), list(zip(prediction_val_x, prediction_val_y)))
    return np.linalg.norm(loss, 2, axis=1)


class PeakSignalToNoiseRatio(BaseRegressionMetric):
    __provider__ = 'psnr'

    annotation_types = (SuperResolutionAnnotation, ImageInpaintingAnnotation, )
    prediction_types = (SuperResolutionPrediction, ImageInpaintingPrediction, )

    @classmethod
    def parameters(cls):
        parameters = super().parameters()
        parameters.update({
            'scale_border': NumberField(optional=True, min_value=0, default=4, description="Scale border."),
            'color_order': StringField(
                optional=True, choices=['BGR', 'RGB'], default='RGB',
                description="The field specified which color order BGR or RGB will be used during metric calculation."
            )
        })

        return parameters

    def __init__(self, *args, **kwargs):
        super().__init__(self._psnr_differ, *args, **kwargs)
        self.meta['target'] = 'higher-better'

    def configure(self):
        super().configure()
        self.scale_border = self.get_value_from_config('scale_border')
        color_order = self.get_value_from_config('color_order')
        channel_order = {
            'BGR': [2, 1, 0],
            'RGB': [0, 1, 2]
        }
        self.meta['postfix'] = 'Db'
        self.channel_order = channel_order[color_order]

    def _psnr_differ(self, annotation_image, prediction_image):
        prediction = np.asarray(prediction_image).astype(np.float)
        ground_truth = np.asarray(annotation_image).astype(np.float)

        height, width = prediction.shape[:2]
        prediction = prediction[
            self.scale_border:height - self.scale_border,
            self.scale_border:width - self.scale_border
        ]
        ground_truth = ground_truth[
            self.scale_border:height - self.scale_border,
            self.scale_border:width - self.scale_border
        ]
        image_difference = (prediction - ground_truth) / 255.  # rgb color space

        r_channel_diff = image_difference[:, :, self.channel_order[0]]
        g_channel_diff = image_difference[:, :, self.channel_order[1]]
        b_channel_diff = image_difference[:, :, self.channel_order[2]]

        channels_diff = (r_channel_diff * 65.738 + g_channel_diff * 129.057 + b_channel_diff * 25.064) / 256

        mse = np.mean(channels_diff ** 2)
        if mse == 0:
            return np.Infinity

        return -10 * math.log10(mse)


def angle_differ(gt_gaze_vector, predicted_gaze_vector):
    return np.arccos(
        gt_gaze_vector.dot(predicted_gaze_vector) / np.linalg.norm(gt_gaze_vector)
        / np.linalg.norm(predicted_gaze_vector)
    ) * 180 / np.pi


class AngleError(BaseRegressionMetric):
    __provider__ = 'angle_error'

    annotation_types = (GazeVectorAnnotation, )
    prediction_types = (GazeVectorPrediction, )

    def __init__(self, *args, **kwargs):
        super().__init__(angle_differ, *args, **kwargs)


def _ssim(annotation_image, prediction_image):
    prediction = np.asarray(prediction_image).astype(np.uint8)
    ground_truth = np.asarray(annotation_image).astype(np.uint8)
    mu_x = np.mean(prediction)
    mu_y = np.mean(ground_truth)
    var_x = np.var(prediction)
    var_y = np.var(ground_truth)
    sig_xy = np.mean((prediction - mu_x)*(ground_truth - mu_y))/(np.sqrt(var_x*var_y))
    c1 = (0.01 * 2**32-1)**2
    c2 = (0.03 * 2**32-1)**2
    mssim = (2*mu_x*mu_y + c1)*(2*sig_xy + c2)/((mu_x**2 + mu_y**2 + c1)*(var_x + var_y + c2))
    return mssim

class StructuralSimilarity(BaseRegressionMetric):
    __provider__ = 'ssim'

    annotation_types = (ImageInpaintingAnnotation, )
    prediction_types = (ImageInpaintingPrediction, )

    def __init__(self, *args, **kwargs):
        super().__init__(_ssim, *args, **kwargs)
        self.meta['target'] = 'higher-better'
