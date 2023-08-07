import numpy as np
import xarray as xr
import dask
import dask.array as da
from dask.distributed import Client
import math
from itertools import product

import subprocess
import sys

try:
    import morton
except ImportError:
    subprocess.check_call([sys.executable, '-m', 'pip', 'install', 'morton-py'])
finally:
    import morton


def node_assignment(cube_side: int):
    """
    Ryan's node assignment code that solves the Color-matching problem

    :param cube_side: Length of the cube side to be assigned to nodes
    """
    def get_bounds(idx:int, mx:int):
        """Helper for requesting valid indicies around an index"""
        return max(idx-1, 0), min(idx+2, mx)

    # There are 34 availble nodes that are 1-indexed
    colors = np.arange(34) + 1
    # An array for holding how often a node has been assigned too.
    color_counts = np.zeros_like(colors)
    # An array to store the node assignment the 8192^3 data is broken into 512^3
    # sub-cubes so we need to 8192/512=16 assignments along each dimension


    nodes = np.zeros([cube_side, cube_side, cube_side], dtype=int)

    # iterate through the position indicies in `nodes` and assign available 
    # nodes greedily such that there is not a matching node within the 
    # (8 + 9 + 9) 26 cube neighborhood.
    for i, j, k in product(np.arange(cube_side), np.arange(cube_side), np.arange(cube_side)):
        if nodes[i, j, k] == 0:
            neighbor_colors = nodes[
                slice(*get_bounds(i, cube_side)),
                slice(*get_bounds(j, cube_side)),
                slice(*get_bounds(k, cube_side))
            ]
            avail_colors = np.setdiff1d(colors, neighbor_colors.flatten())
            color_idxs = avail_colors-1
            greedy_color_idx = np.argmin(color_counts[color_idxs])
            greedy_color = colors[color_idxs[greedy_color_idx]]
            color_counts[color_idxs[greedy_color_idx]] += 1
            
            nodes[i, j, k] = greedy_color
    
    return nodes



# ChatGPT
def split_zarr_group(ds, smaller_size, dims):
    """
    Takes an xarray group of arrays and splits it into smaller groups
    E.g. it takes a 2048^3 group of 6 arrays and returns 64 groups of 512^3

    Parameters:
        ds (xarray.Dataset): The Xarray group you want to split
        smaller_size (int): Size of the chunks along each dimension.
        dims (tuple): Names of the dimensions in order (dim_0, dim_1, dim_2). This can be gotten in reverse order using [dim for dim in data_xr.dims]
        
    Returns:
        list[xarray.Dataset]: A list of lists of lists of smaller Datasets.
    """

    # Calculate the number of chunks along each dimension
    num_chunks = [ds.dims[dim] // smaller_size for dim in dims]

    # I want this to be a 3D list of lists
    outer_dim = []
    
    range_list = [] # Where chunks start and end. Needed for Mike's code to find correct chunks to access

    for i in range(num_chunks[0]):
        mid_dim = []
        for j in range(num_chunks[1]):
            inner_dim = []

            for k in range(num_chunks[2]):
                # Select the chunk from each DataArray
                # These are first distributed along the last (i.e. the x)-index
                chunk = ds.isel(
                    {dims[0]: slice(i * smaller_size, (i + 1) * smaller_size), # nnz
                     dims[1]: slice(j * smaller_size, (j + 1) * smaller_size), # nny
                     dims[2]: slice(k * smaller_size, (k + 1) * smaller_size)} # nnx
                )
                inner_dim.append(chunk)
                
                a = []
                a.append([i * smaller_size, (i + 1) * smaller_size])
                a.append([j * smaller_size, (j + 1) * smaller_size])
                a.append([k * smaller_size, (k + 1) * smaller_size])
                
                range_list.append(a)

            mid_dim.append(inner_dim)

        outer_dim.append(mid_dim)

    return outer_dim, range_list



def list_fileDB_folders():
    return [f'/home/idies/workspace/turb/data{str(d).zfill(2)}_{str(f).zfill(2)}/zarr/'  for f in range(1,4) for d in range(1,13)]



def merge_velocities(data_xr, chunk_size_base=64, use_dask=True, n_dask_workers=2):
    """
        Merge the 3 velocity components/directions - such merging exhibits faster 3-component reads. This is a Dask lazy computation
        
        :param data_xr: the dataset (Xarray group) with 3 velocity components to merge
        :param use_dask: Whether to run single-threaded or use Dask. I'm having problems with Dask:Nanny on SciServer Compute - https://github.com/dask/distributed/issues/3955
    """
    
    if use_dask:
        client = Client(n_workers=n_dask_workers)

    # Merge Velocities into 1
    b = da.stack([data_xr['u'], data_xr['v'], data_xr['w']], axis=3)
    # Make into correct chunk sizes
    b = b.rechunk((chunk_size_base,chunk_size_base,chunk_size_base,3))
    
    
    

    # Drop individual velocities
    result = data_xr.drop_vars(['u', 'v', 'w'])
    # Add joined velocity to original group
    result['velocity'] = xr.DataArray(b, dims=('nnz', 'nny', 'nnx', 'velocity component (xyz)'))
    
    if use_dask:
        client.close()

    return result



def morton_pack(array_cube_side, x,y,z):
    bits = int(math.log(array_cube_side, 2))
    mortoncurve = morton.Morton(dimensions = 3, bits = bits)

    return mortoncurve.pack(x, y, z)


def morton_order_cube(cube_side: int):
    """
    For a 3D list (list of lists of lists), return the z-order of those lists
    
    :param cube_side: Length of the cube side to be z-ordered
    """

    z_order = []
    # Z-order the cube of points so they "linearize" far from each other
    for i in range(cube_side):
        a = []
        for j in range(cube_side):
            b = []
            for k in range(cube_side):
                z_position = morton_pack(cube_side, i,j,k)
                b.append(z_position)
            a.append(b)
        z_order.append(a)

    return z_order



@dask.delayed
def write_to_disk_dask(dest_groupname, current_array, encoding):
    return write_to_disk(dest_groupname, current_array, encoding)


def write_to_disk(dest_groupname, current_array, encoding):
    current_array.to_zarr(store=dest_groupname,
        mode="w",
        encoding = encoding)