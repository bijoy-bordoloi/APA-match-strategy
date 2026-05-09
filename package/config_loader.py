import json
import os
import logging

logger = logging.getLogger()
logger.setLevel(logging.INFO)

# Config loading abstraction — easily switch between S3/local
_config_cache = {}

def _load_config_from_local(filename: str) -> dict:
    """Load config from local file (current approach)."""
    path = f"configurations/{filename}"
    with open(path, 'r') as f:
        if filename.endswith('.csv'):
            import csv
            return list(csv.DictReader(f))
        else:
            return json.load(f)


def _load_config_from_s3(bucket: str, key: str):
    """Load config from S3 (future approach)."""
    import boto3
    s3 = boto3.client('s3')
    response = s3.get_object(Bucket=bucket, Key=key)
    content = response['Body'].read().decode('utf-8')

    if key.endswith('.csv'):
        import csv
        import io
        return list(csv.DictReader(io.StringIO(content)))
    else:
        return json.loads(content)


def load_config(filename: str) -> dict:
    """Load config from source (local or S3 based on env var)."""
    if filename in _config_cache:
        return _config_cache[filename]

    # Check if S3 is configured
    s3_bucket = os.environ.get("CONFIG_S3_BUCKET")

    if s3_bucket:
        logger.info(f"Loading {filename} from S3 bucket: {s3_bucket}")
        config = _load_config_from_s3(s3_bucket, f"configs/{filename}")
    else:
        logger.info(f"Loading {filename} from local file")
        config = _load_config_from_local(filename)

    _config_cache[filename] = config
    return config


def get_matches_data() -> dict:
    """Get match schedule."""
    return load_config("8ball-matches.json")


def get_rosters_data() -> dict:
    """Get team rosters."""
    return load_config("8ball-rosters.json")


def get_schedule_csv() -> list:
    """Get player schedule CSV."""
    return load_config("AVL-schedule.csv")
