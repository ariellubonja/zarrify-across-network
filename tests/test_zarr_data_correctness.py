"""
Ariel Lubonja
2024-01-10

Check whether the written NCAR data is correct by comparing the
original data with the written Zarr data.
Tests in the range [start_timestep, end_timestep].
"""

import unittest
import zarr
from dask.array.utils import assert_eq
import dask.array as da
import yaml
import os
from parameterized import parameterized

from src.dataset import NCAR_Dataset


config = {}
with open('config.yaml', 'r') as file:
    config = yaml.safe_load(file)
dataset_name = os.environ.get('DATASET')
start_timestep = int(os.environ.get('START_TIMESTEP', 40))
end_timestep = int(os.environ.get('END_TIMESTEP', 40))
write_mode = str(os.environ.get('WRITE_MODE', 'prod'))


# Cannot call class method using Parameterized, so have to add this fn. outside the class
def generate_data_correctness_tests():
    global config, dataset_name, start_timestep, end_timestep, write_mode
    if write_mode != 'prod' and write_mode != 'back':
        raise ValueError("prod_or_backup must be either 'prod' or 'back'")
    
    dataset_config = config['datasets'][dataset_name]
    write_config = config['write_settings']

    print("start_timestep: ", start_timestep)
    print("end_timestep: ", end_timestep)

    dataset = NCAR_Dataset(
        name=dataset_name,
        location_paths=dataset_config['location_paths'],
        desired_zarr_chunk_size=write_config['desired_zarr_chunk_length'],
        desired_zarr_array_length=write_config['desired_zarr_array_length'],
        write_mode='prod',
        start_timestep=start_timestep,
        end_timestep=end_timestep
    )

    test_params = []
    for timestep in range(start_timestep, end_timestep + 1):
        print("Current timestep: ", timestep)
        lazy_zarr_cubes, range_list = dataset.transform_to_zarr(timestep)
        destination_paths, chunk_morton_order = dataset.get_zarr_array_destinations(timestep, range_list)

        for original_data_cube, written_zarr_cube, morton_idx in zip(lazy_zarr_cubes, destination_paths, chunk_morton_order.values()):
            # no need to .sel(). Original subcube should already be selected
            test_params.append((original_data_cube, written_zarr_cube))

    print("Done generating tests. Len of test_params: ", len(test_params))
    return test_params


# TODO tons of duplicated code with test_zarr_attributes.py
class VerifyZarrDataCorrectness(unittest.TestCase):
    # Cannot have setUp or setupClass because they don't work with Parameterized

    @parameterized.expand(generate_data_correctness_tests())
    def test_zarr_group_data(self, original_subarray, zarr_group_path):
        '''
        Verify correctness of data contained in each zarr group against
        the original data file (NCAR NetCDF)

        Args:
            original_subarray (xarray.Dataset): (Sub)Array of original data that was written as zarr to zarr_group_path
            zarr_group_path (str): Location of the sub-chunked data as a Zarr Group
        '''
        zarr_group = zarr.open_group(zarr_group_path, mode='r')
        print("Comparing original 512^3 with ", zarr_group_path)
        for var in original_subarray.data_vars:
            assert_eq(original_subarray[var].data, da.from_zarr(zarr_group[var]))
            if config['general_settings']['verbose']:
                print(var, " OK")

