from .base_regression_model import BaseRegressionModel
from .decision_tree_regression import DecisionTreeRegressionModel
from .light_gbm_regression import LightGBMRegressionModel
from .linear_regression import LinearRegressionModel
from .random_forest_regression import RandomForestRegressionModel
from .xgboost_regression import XGBoostRegressionModel

__all__ = [
    "DecisionTreeRegressionModel",
    "LightGBMRegressionModel",
    "LinearRegressionModel",
    "RandomForestRegressionModel",
    "XGBoostRegressionModel",
]


def make_model(model: str) -> BaseRegressionModel:
    if model in __all__:
        return globals()[model]()
    else:
        raise Exception(f"The agent name {model} does not exist")
