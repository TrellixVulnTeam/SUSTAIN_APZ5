import pandas as pd
import numpy as np
from sklearn.preprocessing import MinMaxScaler, StandardScaler
from gluonts.dataset.field_names import FieldName
from gluonts.dataset.common import ListDataset
from preprocess.download_data import download_covid_data


def series_place_holding(series, prediction_length):
    from datetime import timedelta
    max_date = series.index[-1]
    for i in range(1, prediction_length + 1):
        series.loc[max_date + timedelta(days=i)] = 0
    return series


def get_target(series, countries, predict_len):
    # series (should def a function)，最后添加place holding用来预测
    # process_series = series.groupby('Country/Region').sum().drop(['Lat', 'Long'], axis=1).loc[countries, :].T[
    #     countries].fillna(0)
    process_series = series[countries]
    process_series.index = pd.to_datetime(process_series.index)

    # prediction place holding
    prediction_length = predict_len
    process_series = series_place_holding(process_series, prediction_length)

    process_series_diff = process_series.diff()[1:]  # 预测增量
    # process_series_diff = 1 + process_series_diff.pct_change().fillna(0).replace([np.inf, -np.inf], 0)[1:]  # 预测比例

    # define the parameters of the dataset
    metadata = {'num_series': len(countries),
                          'num_steps': len(process_series_diff),
                          'prediction_length': prediction_length,
                          'context_length': prediction_length,
                          'freq': '1D',
                          'start': [pd.Timestamp(process_series_diff.index[0], freq='1D')
                                    for _ in range(len(countries))]
                          }
    # scaler， 对每一个国家的不同日期进行sacle，column=国家
    # target_scaler = StandardScaler().fit(process_series_diff.iloc[:-metadata['prediction_length']])
    # 在这里构造代预测的数据
    # num_series, series length
    # target = target_scaler.transform(process_series_diff).T
    target = process_series_diff.values.T
    target_scaler = None

    return metadata, target_scaler, target, process_series, process_series_diff


def get_static(indicators, countries, indicator_year):
    # static covariate (indicators)
    # num_series, indicator num
    indicators_values = indicators.pivot_table(index=['Indicator', 'Unit'], columns='Country', values=indicator_year)[countries].fillna(0).T
    # 对每一个indicator不同国家的值进行scale
    covariate_s = MinMaxScaler().fit_transform(indicators_values)
    return covariate_s


def get_dynamic(policy, policy_names, countries, process_series_diff):
    # dynamic covariate（time feature and policies）
    # num_series, num policies, series length + (prediction length)
    covariate_d = []
    for policy_name in policy_names:
        tmp = policy[['entity', 'date', policy_name]].pivot_table(index='date', columns='entity', values=policy_name)
        for x in countries:
            if x not in tmp.columns:
                tmp[x] = 0  # some countries miss some policy data, we fill it with zeros
        tmp = tmp[countries]
        tmp.index = pd.to_datetime(tmp.index)

        # if the policy data can not be update, ffill it
        for index in process_series_diff.index:
            if index not in tmp.index:
                tmp.loc[index] = np.nan
        tmp = tmp.ffill()

        tmp = tmp.sort_index()
        tmp = tmp.loc[process_series_diff.index[0]:process_series_diff.index[-1]].fillna(0)
        tmp_value = MinMaxScaler().fit_transform(tmp).T
        # tmp_value = tmp.T.values
        covariate_d.append(tmp_value)
    covariate_d = np.array(covariate_d).transpose(1, 0, 2)
    return covariate_d


def get_train_data(
        type,
        series_category: str = 'confirmed',
        indicator_year: int = 2019,
        predict_len: int = 10,
):
    """
    :param series_category: 'confirmed', 'deaths', 'recovered'
    :param indicator_year:
    :return:
    """
    # 拿2019年的indicators
    # indicators = pd.read_excel('raw_data/indicators.xlsx')[
    #     ['Country', 'Indicator', 'Unit', indicator_year]]
    indicators = pd.read_excel('raw_data/SUSTAIN model indicator data, as of July 5, 2021_OVERALL_World.xlsx')
    indicators['country'] = indicators['country'].str.replace('United States of America', 'United States')
    indicator_years = [2014, 2015, 2016, 2017, 2018, 2019, 2020, 2021]
    indicators[indicator_years] = indicators[indicator_years].ffill(axis=1)

    indicators = indicators[['indicator', 'country', 'unit', indicator_year]].dropna()
    indicators = indicators.rename(columns={'country': 'Country', 'indicator': 'Indicator', 'unit': 'Unit'})

    if type == 'business':
        policies = pd.read_csv('raw_data/policies_all_countries.csv')
    else:
        policies = pd.read_csv('raw_data/future_policies.csv')
    series = pd.read_csv('raw_data/COVID/time_series_covid19_' + series_category + '_global.csv', index_col=0)

    countries = sorted(list(
        set(policies.entity).intersection(set(series.columns)).intersection(set(indicators['Country']))))
#     print(countries)
    indicator_names = list(set(indicators.Indicator))
    policy_names = [x for x in list(policies.columns) if x not in ['entity', 'iso', 'date']]

    metadata, target_scaler, target, process_series, process_series_diff = get_target(series, countries, predict_len)
    covariate_s = get_static(indicators, countries, indicator_year=indicator_year)
    covariate_d = get_dynamic(policies, policy_names, countries, process_series_diff)

    train_ds = ListDataset([{FieldName.TARGET: target,
                             FieldName.START: start,
                             FieldName.FEAT_STATIC_REAL: fsr,
                             FieldName.FEAT_DYNAMIC_REAL: fdr,
                             }
                            for (target, start, fsr, fdr) in zip(target[:, :-metadata['prediction_length']],
                                                                 metadata['start'],
                                                                 covariate_s,
                                                                 covariate_d[:, :,
                                                                 :-metadata['prediction_length']],
                                                                 )],
                           freq=metadata['freq'])

    test_ds = ListDataset([{FieldName.TARGET: target,
                            FieldName.START: start,
                            FieldName.FEAT_STATIC_REAL: fsr,
                            FieldName.FEAT_DYNAMIC_REAL: fdr,
                            }
                           for (target, start, fsr, fdr) in zip(target,
                                                                metadata['start'],
                                                                covariate_s,
                                                                covariate_d,
                                                                )],
                          freq=metadata['freq'])

    return metadata, process_series, train_ds, test_ds, target_scaler, countries






