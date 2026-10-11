from datetime import date, datetime

from numpy import abs, cos, pi, sin
from pandas import DataFrame, Series, cut
from statsmodels.tsa.stattools import pacf

DAYS_IN_MONTH = 30
QUARTERS_IN_YEAR = 4

SEASONS = [
    ("winter", (date(2000, 1, 1), date(2000, 3, 20))),
    ("spring", (date(2000, 3, 21), date(2000, 6, 20))),
    ("summer", (date(2000, 6, 21), date(2000, 9, 22))),
    ("autumn", (date(2000, 9, 23), date(2000, 12, 20))),
    ("winter", (date(2000, 12, 21), date(2000, 12, 31))),
]

SESSION_BINS = [0, 4, 8, 12, 16, 20, 24]
SESSION_LABELS = ["Late Night", "Early Morning", "Morning", "Noon", "Eve", "Night"]


def get_season(time: datetime) -> str:
    dt = time.date().replace(year=2000)
    return next(season for season, (start, end) in SEASONS if start <= dt <= end)


def encode_categorical_data(dataframe: DataFrame) -> None:
    # "str" named explicitly: pandas 3 still folds it into "object" here but warns, once per
    # call, and the forecast loop calls this for every hour of every pollutant.
    obj_columns = dataframe.select_dtypes(include=["object", "str"]).columns
    dataframe[obj_columns] = dataframe[obj_columns].astype("category")
    cat_columns = dataframe.select_dtypes("category").columns
    dataframe[cat_columns] = dataframe[cat_columns].apply(lambda x: x.cat.codes)


def encode_cyclic_data(
    features: DataFrame, col: str, data: DataFrame | Series, max_value: int
) -> None:
    features[f"{col}_cos"] = cos(2 * pi * data / max_value)
    features[f"{col}_sin"] = sin(2 * pi * data / max_value)


def generate_lag_features(target: Series, lags: int) -> DataFrame:
    partial = Series(
        data=pacf(
            target, nlags=lags if lags < target.size // 2 else target.size // 2 - 1
        )
    )
    lags = list(partial[abs(partial) >= 0.2].index)
    if 0 in lags:
        # Do not consider itself as a lag feature
        lags.remove(0)
    features = DataFrame()
    for lag in lags:
        features[f"lag_{lag}"] = target.shift(lag)
    return features


def generate_time_features(target) -> DataFrame:
    features = DataFrame()
    encode_cyclic_data(features, "month", target.index.month, 12)
    encode_cyclic_data(features, "day", target.index.day, DAYS_IN_MONTH)
    encode_cyclic_data(features, "hour", target.index.hour, 24)
    encode_cyclic_data(
        features,
        "week_of_year",
        Series(target.index.isocalendar().week, dtype="int64"),
        52,
    )
    encode_cyclic_data(features, "day_of_week", target.index.dayofweek, 7)
    encode_cyclic_data(features, "day_of_year", target.index.dayofyear, 365)
    encode_cyclic_data(features, "quarter", target.index.quarter, QUARTERS_IN_YEAR)
    encode_cyclic_data(
        features, "days_in_month", target.index.days_in_month, DAYS_IN_MONTH
    )
    features["isMonthStart"] = target.index.is_month_start
    features["isMonthEnd"] = target.index.is_month_end
    features["isQuarterStart"] = target.index.is_quarter_start
    features["isQuarterEnd"] = target.index.is_quarter_end
    features["isYearStart"] = target.index.is_year_start
    features["isYearEnd"] = target.index.is_year_end
    features["isLeapYear"] = target.index.is_leap_year
    features["isWeekend"] = (
        target.index.to_series()
        .apply(lambda x: 0 if x.dayofweek in (5, 6) else 1)
        .values
    )

    season = DataFrame(target.index.to_series().apply(get_season).values)
    encode_categorical_data(season)
    encode_cyclic_data(features, "season", season, QUARTERS_IN_YEAR)

    session = DataFrame(
        cut(
            target.index.hour,
            bins=SESSION_BINS,
            labels=SESSION_LABELS,
            include_lowest=True,
        )
    )
    encode_categorical_data(session)
    encode_cyclic_data(features, "session", session, len(SESSION_LABELS))

    features = features.set_index(target.index)
    return features


def lags_of(feature_names: list) -> list[int]:
    """The lag offsets a trained model uses, read back from its `lag_<n>` feature names."""
    return sorted(
        int(name.removeprefix("lag_"))
        for name in feature_names
        if name.startswith("lag_") and name.removeprefix("lag_").isdigit()
    )


def fixed_lag_features(target: Series, lags: list[int]) -> DataFrame:
    features = DataFrame(index=target.index)
    for lag in lags:
        features[f"lag_{lag}"] = target.shift(lag)
    return features


def generate_features(
    target: Series, lags: int = 24, fixed_lags: list[int] | None = None
) -> DataFrame:
    """Lag and time features for `target`.

    Training picks the lags by partial autocorrelation. Inference must NOT: it passes the
    model's own lags as `fixed_lags`. The forecast loop used to rerun the selection on the last
    51 values, which picks a different set; any lag the model was trained on and the rerun
    dropped raised a KeyError at `features[model_features]`, and every hour came back NaN.
    """
    lag_features = (
        generate_lag_features(target, lags)
        if fixed_lags is None
        else fixed_lag_features(target, fixed_lags)
    )
    time_features = generate_time_features(target)
    features = (
        time_features
        if len(lag_features.index) == 0
        else lag_features.join(time_features, how="inner")
    )
    return features
