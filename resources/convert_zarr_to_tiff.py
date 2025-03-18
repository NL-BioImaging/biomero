# Copyright 2023 Torec Luik, Maarten Paul
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import argparse
import os
import zarr
import tifffile as tf
import dask.array as da
import numpy as np
import logging

def rearrange_dimensions(volume, axes, target="TZCYX"):
    """
    Rearrange dimensions of an array to match the target dimension order
    
    Parameters
    ----------
    volume : dask array or numpy array
        Image data from zarr or tifffile's asarray()
    axes : str or list
        Axes string or list of dimension names (e.g., 'TZCYX', 'YX', ['t', 'c', 'z', 'y', 'x'], etc.)
    target : str, optional
        String specifying the desired dimension order, default is "XYZCT"
        Only dimensions present in the input axes will be used
        
    Returns
    -------
    tuple-like object containing:
        - The rearranged array with dimensions ordered according to target
        - The new dimension order string
    """
    # Convert list of dimension names to string if needed
    if isinstance(axes, list):
        axes = ''.join([dim[0].upper() for dim in axes])
    
    # Standardize to uppercase
    axes = axes.upper()
    target = target.upper()
    
    # Validate input volume
    if not isinstance(volume, (np.ndarray, da.Array)):
        raise TypeError("Input volume must be a numpy.ndarray or dask.array")
    
    # Validate axes dimensions match array dimensions
    if len(axes) != volume.ndim:
        raise ValueError(f"Axes string '{axes}' length {len(axes)} does not match array dimensions {volume.ndim}")
    
    # Some TIFF files use 'S' for samples/channels, convert to 'C' for consistency
    axes = axes.replace('S', 'C')
    
    # Filter target to only include dimensions that exist in axes
    filtered_target = ''.join([dim for dim in target if dim in axes])
    
    # Create mapping from current positions to new positions
    current_order = axes
    new_order = filtered_target
    
    # If there are dimensions in current_order that aren't in target, append them
    for dim in current_order:
        if dim not in new_order:
            new_order += dim
    
    # Reorder dimensions if needed
    if current_order != new_order:
        # Create list of current positions for each dimension
        source_indices = [current_order.index(dim) for dim in new_order]
        target_indices = list(range(len(new_order)))
        
        # Rearrange dimensions
        result = da.moveaxis(volume, source_indices, target_indices) if isinstance(volume, da.Array) else np.moveaxis(volume, source_indices, target_indices)
    else:
        result = volume
    
    # Return both the array and the new dimension order
    class ReturnValue(tuple):
        """Custom return class to allow both direct access and unpacking"""
        def __new__(cls, img, axes):
            return tuple.__new__(cls, (img, axes))
            
        def __repr__(self):
            return repr(self[0])
            
        # Make the first element (the image) accessible directly
        def __array__(self, dtype=None):
            if isinstance(self[0], da.Array):
                return self[0].compute() if dtype is None else self[0].compute().astype(dtype)
            return np.asarray(self[0], dtype=dtype)
    
    return ReturnValue(result, new_order)

def get_dimension_order(zarr_file, key):
    """
    Extract dimension ordering from OME-Zarr metadata
    
    Parameters
    ----------
    zarr_file : ome-zarr file
        Opened zarr file
    key : str
        Key name for zarr dataset
        
    Returns
    -------
    str or None
        String representing dimension order (e.g., 'TZCYX') or None if can't be determined
    """
    try:
        # Try to get OME-Zarr metadata
        metadata = zarr_file.attrs.asdict()
        if 'multiscales' in metadata:
            # Extract dimension information from multiscales metadata
            dimensions = metadata['multiscales'][0]['axes']
            # Get dimension names in order
            dim_order = [dim['name'] for dim in dimensions]
            # Also try to get additional metadata if available
            try:
                dim_types = [dim.get('type', '') for dim in dimensions]
                logging.info(f"Dimension types: {dim_types}")
            except Exception as e:
                logging.debug(f"Could not extract dimension types: {e}")
            dim_order = ''.join(letter.upper() for letter in dim_order)
            return dim_order
            
    except (KeyError, AttributeError) as e:
        logging.warning(f"Could not extract OME dimension order: {e}")
        # Fall back to guessing from shape
        return None
   
def convert_zarr_to_tiff(zarr_file_path, key=None, output_file=None):
    """
    Convert OME-Zarr file to TIFF maintaining appropriate dimension order
    
    Parameters
    ----------
    zarr_file_path : str
        Path to input zarr file
    key : str, optional
        Key name for zarr dataset. If None, the first available key will be used
    output_file : str, optional
        Path for output tiff file. If None, will be derived from input filename
    
    Raises
    ------
    ValueError
        If no keys are found in the zarr file or if specified key doesn't exist
    """
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    try:
        # Open the Zarr file
        with zarr.open(zarr_file_path, mode='r') as zarr_file:
            available_keys = list(zarr_file.keys())

            if len(available_keys) == 0:
                raise ValueError("No keys found in the Zarr file.")
            
            if key is None:
                key = available_keys[0]
            elif key not in available_keys:
                raise ValueError(f"Specified key '{key}' not found in the Zarr file.")

            # Get dimension order from metadata
            dim_order = get_dimension_order(zarr_file, key)
            logger.info(f"Detected dimension order: {dim_order}")
            
            # Create Dask array from zarr
            dask_image_data = da.from_zarr(zarr_file[key])
            logger.info(f"Original data shape: {dask_image_data.shape}")
            
            # Set appropriate target dimension order based on input data
            if dim_order == 'CYX':
                target = "YXC"  # ImageJ compatible format for RGB images
            else:
                target = "TZCYX"  
                
            # Rearrange dimensions to match target order
            dask_image_data, ordered_dims = rearrange_dimensions(dask_image_data, dim_order, target)
            logger.info(f"Reordered data shape: {dask_image_data.shape} with dimensions {ordered_dims}")
            
            if output_file is None:
                # to make it easier to work with current BIOMERO workflow save as .tif instead of ome.tif 
                output_file = os.path.splitext(zarr_file_path)[0] + f".{key}.tif"

            dask_image_data.persist()
            # Write to TIFF with appropriate format settings
            if ordered_dims == "YXC":
                tf.imwrite(output_file, dask_image_data, 
                                planarconfig='contig',
                                # imagej=True,
                                  )
            else:   
                # Create metadata with actual dimension order
                metadata = {'axes': ordered_dims}
                logger.info(f"Using metadata: {metadata}")
         
                tf.imwrite(output_file, 
                        dask_image_data,
                        photometric='minisblack',
                        ome=True,
                        metadata=metadata)
            
            logger.info(f"Conversion completed successfully with key: '{key}'.")
            logger.info(f"Output TIFF file: '{output_file}'")
    
    except Exception as e:
        logger.error(f"An error occurred: {e}")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert a Zarr file to a TIFF file.")
    parser.add_argument("zarr_file", help="Path to the input Zarr file")
    parser.add_argument("--key", help="Key name for the Zarr dataset to convert")
    parser.add_argument("--output", help="Path for the output TIFF file (optional)")

    args = parser.parse_args()

    convert_zarr_to_tiff(args.zarr_file, args.key, args.output)