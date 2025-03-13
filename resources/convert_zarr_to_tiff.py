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
import logging


def get_dimension_order(zarr_file, key):
    """
    Extract dimension ordering from OME-Zarr metadata
    
    Args:
        zarr_file: Opened zarr file
        key: Key name for zarr dataset
        
    Returns:
        List of dimension names in order
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
            return dim_order
    except (KeyError, AttributeError) as e:
        logging.warning(f"Could not extract OME dimension order: {e}")
        # Fall back to guessing from shape
        return None
   
def convert_zarr_to_tiff(zarr_file_path, key=None, output_file=None):
    """
    Convert OME-Zarr file to TIFF maintaining dimension order
    
    Args:
        zarr_file_path: Path to input zarr file
        key: Key name for zarr dataset
        output_file: Path for output tiff file
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

            if output_file is None:
                #to make it easier to work with current BIOMERO workflow save as .tif instead of ome.tif 
                output_file = os.path.splitext(zarr_file_path)[0] + f".{key}.tif"

            # Create metadata with actual dimension order
            metadata = {'axes': ''.join(dim.upper() for dim in dim_order)}
            logger.info(f"Using metadata: {metadata}")

            # Write to TIFF - keeping original dimension order
            dask_image_data.persist()
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