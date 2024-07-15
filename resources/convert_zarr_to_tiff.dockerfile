# Use python:3.9-slim as base image
FROM python:3.9-slim

# Install required libraries with specific versions
RUN pip install zarr==2.16.1 tifffile==2020.9.3 dask[array]==2021.3.0 numpy==1.26.4

# Copy the script into the container
COPY convert_zarr_to_tiff.py /app/convert_zarr_to_tiff.py

# Set the working directory
WORKDIR /app

# Run the script with the provided arguments
ENTRYPOINT ["python", "/app/convert_zarr_to_tiff.py"]
