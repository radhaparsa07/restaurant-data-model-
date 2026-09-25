\# CMS Hospital Data Downloader



This project downloads datasets from the CMS Provider Data API that are associated with the `Hospitals` theme.



The script:



\- Retrieves CMS dataset metadata from the API

\- Identifies all datasets associated with `Hospitals`

\- Finds the CSV download URL for each dataset

\- Downloads and processes multiple datasets in parallel

\- Converts CSV column names to snake\_case

\- Tracks dataset metadata between runs

\- Downloads only new or modified datasets

\- Saves processed files locally

\- Produces a run summary showing downloaded, skipped, and failed datasets



\## Requirements



\- Python 3.9+

\- `requests`



Install the dependency with:



```bash

pip install -r requirements.txt

