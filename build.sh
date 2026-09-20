#!/usr/bin/env bash
# Exit on error
set -o errexit

# Install production dependencies
pip install -r requirements.txt

# Collect static files for WhiteNoise
python manage.py collectstatic --noinput

# Run database migrations on PostgreSQL
python manage.py migrate
