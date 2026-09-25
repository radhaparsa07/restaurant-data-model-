import csv
import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import requests


API_URL = (
    "https://data.cms.gov/provider-data/api/1/"
    "metastore/schemas/dataset/items"
)

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data" / "hospitals"
METADATA_DIR = BASE_DIR / "metadata"
METADATA_FILE = METADATA_DIR / "run_metadata.json"

MAX_WORKERS = 5
REQUEST_TIMEOUT = 60


def snake_case(value):
    """Convert a column name to snake_case."""
    value = value.replace("’", "'").replace("‘", "'")
    value = value.replace("&", " and ")

    value = re.sub(r"[^A-Za-z0-9]+", "_", value)
    value = re.sub(r"_+", "_", value)

    return value.strip("_").lower()


def load_metadata():
    """Load metadata from the previous successful run."""
    if not METADATA_FILE.exists():
        return {}

    with open(METADATA_FILE, "r", encoding="utf-8") as file:
        return json.load(file)


def save_metadata(metadata):
    """Save metadata for the next run."""
    METADATA_DIR.mkdir(parents=True, exist_ok=True)

    with open(METADATA_FILE, "w", encoding="utf-8") as file:
        json.dump(metadata, file, indent=2)


def get_hospital_datasets():
    """Get all CMS datasets with the Hospitals theme."""
    response = requests.get(API_URL, timeout=REQUEST_TIMEOUT)
    response.raise_for_status()

    datasets = response.json()

    return [
        dataset
        for dataset in datasets
        if any(
            theme.lower() == "hospitals"
            for theme in dataset.get("theme", [])
        )
    ]


def get_csv_url(dataset):
    """Find the CSV download URL for a dataset."""
    for distribution in dataset.get("distribution", []):
        media_type = distribution.get("mediaType", "")
        download_url = distribution.get("downloadURL")

        if download_url and media_type.lower() == "text/csv":
            return download_url

    return None


def make_file_name(title, dataset_id):
    """Create a safe output filename."""
    safe_title = re.sub(
        r"[^A-Za-z0-9]+",
        "_",
        title
    ).strip("_").lower()

    return f"{safe_title}_{dataset_id}.csv"


def download_and_process(dataset, download_url):
    """Download a CSV and convert its headers to snake_case."""

    dataset_id = dataset["identifier"]
    title = dataset.get("title", dataset_id)

    output_file = DATA_DIR / make_file_name(title, dataset_id)
    temp_file = DATA_DIR / f"{output_file.stem}.tmp"

    response = requests.get(
        download_url,
        timeout=REQUEST_TIMEOUT
    )
    response.raise_for_status()

    with open(temp_file, "wb") as file:
        file.write(response.content)

    try:
        with open(
            temp_file,
            "r",
            encoding="utf-8-sig",
            newline=""
        ) as source:

            reader = csv.reader(source)

            try:
                headers = next(reader)
            except StopIteration:
                return {
                    "dataset_id": dataset_id,
                    "title": title,
                    "status": "empty_file"
                }

            headers = [snake_case(header) for header in headers]

            with open(
                output_file,
                "w",
                encoding="utf-8",
                newline=""
            ) as target:

                writer = csv.writer(target)
                writer.writerow(headers)

                row_count = 0

                for row in reader:
                    writer.writerow(row)
                    row_count += 1

        temp_file.unlink(missing_ok=True)

        return {
            "dataset_id": dataset_id,
            "title": title,
            "status": "downloaded",
            "rows": row_count,
            "columns": len(headers),
            "output_file": str(output_file)
        }

    except Exception:
        temp_file.unlink(missing_ok=True)
        raise


def main():

    start_time = datetime.now(timezone.utc)

    DATA_DIR.mkdir(parents=True, exist_ok=True)

    previous_metadata = load_metadata()

    datasets = get_hospital_datasets()

    print(f"Hospital datasets found: {len(datasets)}")

    downloads = []
    current_metadata = {}

    for dataset in datasets:

        dataset_id = dataset["identifier"]
        title = dataset.get("title", dataset_id)
        modified = dataset.get("modified")

        download_url = get_csv_url(dataset)

        if not download_url:
            print(f"Skipping {dataset_id}: no CSV download URL")
            continue

        previous = previous_metadata.get(dataset_id)

        current_metadata[dataset_id] = {
            "title": title,
            "modified": modified,
            "download_url": download_url
        }

        # Download if the dataset is new or has changed.
        if (
            previous is None
            or previous.get("modified") != modified
            or previous.get("download_url") != download_url
        ):
            downloads.append(
                (dataset, download_url)
            )

    print(f"Datasets needing download: {len(downloads)}")
    print(f"Datasets skipped: {len(datasets) - len(downloads)}")

    results = []

    if downloads:

        with ThreadPoolExecutor(
            max_workers=MAX_WORKERS
        ) as executor:

            future_map = {
                executor.submit(
                    download_and_process,
                    dataset,
                    download_url
                ): dataset
                for dataset, download_url in downloads
            }

            for future in as_completed(future_map):

                dataset = future_map[future]

                try:
                    result = future.result()
                    results.append(result)

                    print(
                        f"Downloaded: {dataset.get('title')}"
                    )

                except Exception as error:

                    results.append({
                        "dataset_id": dataset.get("identifier"),
                        "title": dataset.get("title"),
                        "status": "failed",
                        "error": str(error)
                    })

                    print(
                        f"FAILED: {dataset.get('title')} - {error}"
                    )

    # Only save metadata for successful downloads.
    successful_ids = {
        result["dataset_id"]
        for result in results
        if result.get("status") == "downloaded"
    }

    for dataset_id in successful_ids:
        current_metadata[dataset_id]["last_successful_run"] = (
            start_time.isoformat()
        )

    # Keep previous metadata for failed datasets.
    for dataset_id, previous in previous_metadata.items():

        if dataset_id not in successful_ids:

            if dataset_id in current_metadata:

                current_metadata[dataset_id][
                    "last_successful_run"
                ] = previous.get("last_successful_run")

    save_metadata(current_metadata)

    completed_time = datetime.now(timezone.utc)

    summary = {
        "run_time_utc": start_time.isoformat(),
        "completed_time_utc": completed_time.isoformat(),
        "hospital_datasets_found": len(datasets),
        "datasets_downloaded": len(
            [
                result
                for result in results
                if result.get("status") == "downloaded"
            ]
        ),
        "datasets_failed": len(
            [
                result
                for result in results
                if result.get("status") == "failed"
            ]
        ),
        "datasets_skipped": len(datasets) - len(downloads),
        "results": results
    }

    print("\nRun summary:")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()