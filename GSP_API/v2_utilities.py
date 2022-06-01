import os
import pandas as pd
import xarray
import datetime
import glob
import netCDF4 as nc
import numpy as np

from flask import make_response, jsonify

from constants import PATH_TO_FORECASTS, PATH_TO_ERA_5, M3_TO_FT3
from v1_functions import reach_to_region, latlon_to_reach

__all__ = [
    'ALL_PRODUCTS', 'PRODUCT_SHORTCUTS', 'NUM_DECIMALS',
    'handle_request', 'get_forecast_dataset', 'get_historical_dataframe', 'get_return_periods_dataframe',
    'dataframe_to_csv_flask_response', 'dataframe_to_jsonify_response', 'new_json_template', 'get_most_recent_date',
]

# Name of all recognized products and their shorthand name for analytics in dict key/value pairs
ALL_PRODUCTS = {
    'forecast': 'fc',
    'forecaststats': 'fcstat',
    'forecastensembles': 'fcens',
    'forecastrecords': 'fcrec',
    'forecastanomalies': 'fcanom',
    'forecastwarnings': 'fcwarn',
    'forecastdates': 'fcdate',
    'hindcast': 'hc',
    'monthlyaverages': 'monavg',
    'dailyaverages': 'dayavg',
    'returnperiods': 'rp',
    'getreachid': 'getid',
    'hydroviewer': 'hv',
}

# Recognized shorthand names for selected products and their proper name in dict key/value pairs
PRODUCT_SHORTCUTS = {
    'stats': 'forecaststats',
    'ensembles': 'forecastensembles',
    'ens': 'forecastensembles',
    'records': 'forecastrecords',
    'monavg': 'monthlyaverages',
    'dayavg': 'dailyaverages',
    'historical': 'hindcast',
    'historicalsimulation': 'hindcast',
    'availabledates': 'forecastdates',
}

# Max number of decimals to show in results
NUM_DECIMALS = 2


def handle_request(request, product, reach_id, return_format):
    data_units = ('cms', 'cfs',)
    return_formats = ('csv', 'json',)

    product = str(product).lower()
    if product not in ALL_PRODUCTS.keys():
        if product not in PRODUCT_SHORTCUTS.keys():
            raise ValueError(f'{product} not recognized. available data products: {list(ALL_PRODUCTS.keys())}')
        product = PRODUCT_SHORTCUTS[product]

    if reach_id is None:
        reach_id = latlon_to_reach(request.args.get('lat', None), request.args.get('lon', None))
    try:
        reach_id = int(reach_id)
    except Exception:
        raise ValueError("reach_id should be an integer corresponding to a valid ID of a stream segment")

    if return_format not in return_formats:
        raise ValueError('format not recognized. must be either "json" or "csv"')

    units = request.args.get('units', 'cms')
    if units not in data_units:
        raise ValueError(f'units not recognized, choose from: {data_units}')

    year = datetime.datetime.utcnow().year
    date = request.args.get('date', 'latest')
    start_date = request.args.get('start_date', datetime.datetime(year=year, month=1, day=1).strftime('%Y%m%d'))
    end_date = request.args.get('end_date', datetime.datetime(year=year, month=12, day=31).strftime('%Y%m%d'))

    ensemble = request.args.get('ensemble', 'all')

    return product, reach_id, return_format, units, date, ensemble, start_date, end_date


def get_forecast_dataset(reach_id, date):
    if date == 'latest':
        date = get_most_recent_date()
    region = reach_to_region(reach_id)
    forecast_dir = os.path.join(PATH_TO_FORECASTS, region, f'{date}.00')

    if not os.path.exists(forecast_dir):
        raise ValueError(f'forecast data not found for date {date} (region: "{region}"). Use YYYYMMDD format.')

    forecast_nc_list = sorted(glob.glob(os.path.join(forecast_dir, "Qout*.nc")), reverse=True)

    if len(forecast_nc_list) == 0:
        raise ValueError('forecast data not found')

    try:
        # combine 52 ensembles
        qout_datasets = []
        ensemble_index_list = []
        for forecast_nc in forecast_nc_list:
            ensemble_index_list.append(int(os.path.basename(forecast_nc)[:-3].split("_")[-1]))
            qout_datasets.append(xarray.open_dataset(forecast_nc).sel(rivid=reach_id).Qout)
        return xarray.concat(qout_datasets, pd.Index(ensemble_index_list, name='ensemble'))
    except Exception as e:
        print(e)
        raise ValueError('Error while reading data from the netCDF files')


def get_historical_dataframe(reach_id: int, units: str) -> pd.DataFrame:
    region = reach_to_region(reach_id)
    historical_data_file = glob.glob(os.path.join(PATH_TO_ERA_5, region, 'Qout*.nc*'))[0]
    template = os.path.join(PATH_TO_ERA_5, 'era5_pandas_dataframe_template.pickle')

    # collect the data in a dataframe
    df = pd.read_pickle(template)
    qout_nc = nc.Dataset(historical_data_file)
    try:
        df['flow'] = qout_nc['Qout'][:, list(qout_nc['rivid'][:]).index(reach_id)]
        qout_nc.close()
    except Exception as e:
        qout_nc.close()
        raise e

    if units == 'cfs':
        df['flow'] = df['flow'].values * M3_TO_FT3
    df.rename(columns={'flow': f'flow_{units}'}, inplace=True)

    return df


def get_return_periods_dataframe(reach_id: int, units: str) -> pd.DataFrame:
    region = reach_to_region(reach_id)
    return_period_file = glob.glob(os.path.join(PATH_TO_ERA_5, region, '*return_periods*.nc*'))
    if len(return_period_file) == 0:
        raise ValueError("Unable to find return periods file")

    # collect the data in a dataframe
    return_periods_nc = xarray.open_dataset(return_period_file[0])
    return_periods_df = return_periods_nc.to_dataframe()
    return_periods_df = return_periods_df[return_periods_df.index == reach_id]
    return_periods_nc.close()

    try:
        del return_periods_df['lon'], return_periods_df['lat']
    except Exception:
        pass

    if units == 'cfs':
        for column in return_periods_df:
            return_periods_df[column] *= M3_TO_FT3
    return return_periods_df


def dataframe_to_csv_flask_response(df: pd.DataFrame, csv_name: str):
    response = make_response(df.to_csv())
    response.headers['content-type'] = 'text/csv'
    response.headers['Content-Disposition'] = f'attachment; filename={csv_name}.csv'
    return response


def dataframe_to_jsonify_response(df: pd.DataFrame, reach_id: int, units: str):
    json_template = new_json_template(reach_id, units, start_date=df.index[0], end_date=df.index[-1])

    # add the columns from the dataframe
    json_template['datetime'] = df.index.tolist()
    json_template.update(df.replace(np.nan, '').to_dict(orient='list'))
    json_template['metadata']['series'] = ['datetime', ] + df.columns.tolist()

    return jsonify(json_template)


def new_json_template(reach_id, units, start_date, end_date):
    return {
        'metadata': {
            'reach_id': reach_id,
            'gen_date': datetime.datetime.utcnow().strftime('%Y-%m-%dY%X+00:00'),
            'start_date': start_date,
            'end_date': end_date,
            'series': [],
            'units': {
                'name': 'streamflow',
                'short': f'{units}',
                'long': f'Cubic {"Meters" if units == "cms" else "Feet"} per Second',
            },
        }
    }


def find_available_dates():
    sample_region_directory = glob.glob(os.path.join(PATH_TO_FORECASTS, "*-geoglows"))[0]
    dates = [d for d in os.listdir(sample_region_directory) if d.split('.')[0].isdigit()]
    if len(dates) > 0:
        return jsonify({"available_dates": dates})
    else:
        return jsonify({"message": "No dates available."}), 204


def find_forecast_warnings(date) -> pd.DataFrame:
    warnings = None

    for region in os.listdir(PATH_TO_FORECASTS):
        # find/check current output datasets
        path_to_region_forecasts = os.path.join(PATH_TO_FORECASTS, region)
        if not os.path.isdir(path_to_region_forecasts):
            continue
        if date == 'most_recent':
            date_folders = sorted([d for d in os.listdir(path_to_region_forecasts)
                                   if os.path.isdir(os.path.join(path_to_region_forecasts, d))],
                                  reverse=True)
            folder = os.path.join(path_to_region_forecasts, date_folders[0])
        else:
            folder = os.path.join(path_to_region_forecasts, date)
            if not os.path.isdir(folder):
                raise ValueError(f'Forecast date {date} was not found')
        # locate the forecast warning csv
        summary_file = os.path.join(folder, 'forecasted_return_periods_summary.csv')
        region_warnings_df = pd.read_csv(summary_file)
        region_warnings_df['region'] = region.replace('-geoglows', '')
        if not os.path.isfile(summary_file):
            continue
        if warnings is None:
            warnings = region_warnings_df
            continue

        warnings = pd.concat([warnings, region_warnings_df], axis=0)

    if warnings is None:
        raise ValueError('Unable to find any warnings csv files for any region for the specified date')

    return warnings


def get_most_recent_date() -> str:
    available_dirs = glob.glob(os.path.join(PATH_TO_FORECASTS, '*'))
    if len(available_dirs) == 0:
        raise ValueError("unable to find any region folders in the forecast directory")
    available_dirs = glob.glob(os.path.join(available_dirs[0], '*'))
    available_dirs = sorted([d for d in available_dirs if os.path.isdir(d)], reverse=True)
    if len(available_dirs) == 0:
        raise ValueError("unable to find forecast results in the region forecast directory")
    return os.path.basename(available_dirs[0]).split('.')[0]
