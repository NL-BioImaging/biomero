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
        zarr_file = zarr.open(zarr_file_path, mode='r')

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

        # Create a Dask array from the specified Zarr key (lazy loading)
        dask_image_data = da.from_zarr(zarr_file[key])

        # Generate the default output file name based on the input file name and key
        input_file_name = os.path.basename(zarr_file_path)
        if output_file is None:
            output_file = os.path.splitext(input_file_name)[0] + f".{key}.tif"

        # Write the Dask array to the TIFF file
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
    parser.add_argument("--output", help="Path to the output TIFF file (optional)")

    args = parser.parse_args()

    convert_zarr_to_tiff(args.zarr_file, args.key, args.output)
