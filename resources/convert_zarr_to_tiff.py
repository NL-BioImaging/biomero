# Copyright 2023 Torec Luik
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


def convert_zarr_to_tiff(zarr_file_path, key=None, output_file=None):
    # Configure logging
    logging.basicConfig(level=logging.INFO)
    logger = logging.getLogger(__name__)

    try:
        # Open the Zarr file
        with zarr.open(zarr_file_path, mode='r') as zarr_file:

            # Get the available keys
            available_keys = list(zarr_file.keys())

            if len(available_keys) == 0:
                raise ValueError("No keys found in the Zarr file.")
            
            # Determine the key to use for conversion
            if key is None:
                # If no key is specified, use the first available key
                key = available_keys[0]
            elif key not in available_keys:
                raise ValueError(f"Specified key '{key}' not found in the Zarr file.")

            # Create a Dask array from the specified Zarr key (Ready for persist)
            dask_image_data = da.from_zarr(zarr_file[key])
            
        # ZARR extracted from OMERO will be (up to) 5D arrays of shape (t, c, z, y, x). 
        # But with 2D images that is (t, c, y, x), or (c, y, x)
        # We want to provide the opposite, (x, y, c, (t)). So invert all dimensions:
        num_dims = len(dask_image_data.shape)
        new_order = tuple(range(num_dims - 1, -1, -1))  # Reverses the dimensions
        dask_image_data = dask_image_data.transpose(new_order)
        # it seems to be (x,y) are still inversed? Inverse those back
        new_order = (1, 0) + tuple(range(2, num_dims))
        dask_image_data = dask_image_data.transpose(*new_order)

        # Generate the default output file name based on the input file name and key
        if output_file is None:
            output_file = os.path.splitext(zarr_file_path)[0] + f".{key}.tif"

        # Write the Dask array to the TIFF file
        dask_image_data.persist()
        tf.imwrite(output_file, dask_image_data)
        
        logger.info(f"Conversion completed successfully with key: '{key}'.")
        logger.info(f"Output TIFF file: '{output_file}'")
    
    except Exception as e:
        # Log the error message and raise the exception
        logger.error(f"An error occurred: {e}")
        raise


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Convert a Zarr file to a TIFF file.")
    parser.add_argument("zarr_file", help="Path to the input Zarr file")
    parser.add_argument("--key", help="Key name for the Zarr dataset to convert")
    parser.add_argument("--output", help="Path for the output TIFF file (optional)")

    args = parser.parse_args()

    convert_zarr_to_tiff(args.zarr_file, args.key, args.output)
