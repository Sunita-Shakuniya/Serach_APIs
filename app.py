# app.py
import os
os.environ['OAUTHLIB_INSECURE_TRANSPORT'] = '1'
import re
import pandas as pd
from flask import Flask, render_template, request, redirect, flash, url_for, session
from werkzeug.utils import secure_filename
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import Flow
from googleapiclient.discovery import build
from io import StringIO
from googleapiclient.errors import HttpError
from serpapi import GoogleSearch
import asyncio
import aiohttp

import requests
app = Flask(__name__)
app.secret_key = 'your_secret_key'
app.config['UPLOAD_FOLDER'] = 'uploads'
app.config['ALLOWED_EXTENSIONS'] = {'csv'}

SCOPES = ['https://www.googleapis.com/auth/spreadsheets']
GOOGLE_CLIENT_SECRETS_FILE = "credentials.json"

SERPAPI_API_KEY = '2257bff8f4e29ee0cddf5af5dd59660ebc12c86709da05c64d12dd2d9e85fcea'


# Initialize the OAuth2 flow
flow = Flow.from_client_secrets_file(
    GOOGLE_CLIENT_SECRETS_FILE,
    scopes=SCOPES,
    redirect_uri='http://localhost:5000/oauth2callback'
)

# Ensure the uploads directory exists
os.makedirs(app.config['UPLOAD_FOLDER'], exist_ok=True)

def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in app.config['ALLOWED_EXTENSIONS']

@app.route('/')
def home():
    return render_template("index.html")


@app.route('/upload', methods=['POST'])
def upload_file():
    # Check if a CSV file was uploaded
    if 'file' in request.files and request.files['file'].filename != '':
        file = request.files['file']
        if file and allowed_file(file.filename):
            filename = secure_filename(file.filename)
            file_path = os.path.join(app.config['UPLOAD_FOLDER'], filename)
            file.save(file_path)

            # Read the CSV file using pandas
            data = pd.read_csv(file_path)
            data.reset_index(inplace=True)
            data.rename(columns={'index': 'ID'}, inplace=True)

            # Save DataFrame to CSV and store file path in session
            processed_file_path = os.path.join(app.config['UPLOAD_FOLDER'], f"processed_{filename}")
            data.to_csv(processed_file_path, index=False)
            session['csv_file_path'] = processed_file_path
            return redirect(url_for('preview'))

    # Check if a Google Sheets URL was provided
    sheet_url = request.form.get('sheet_url')
    if sheet_url:
        data, error = get_google_sheet_data(sheet_url)
        if data is not None:
            data.reset_index(inplace=True)
            data.rename(columns={'index': 'ID'}, inplace=True)

            # Save the data to a CSV file
            processed_file_path = os.path.join(app.config['UPLOAD_FOLDER'], 'google_sheet_data.csv')
            data.to_csv(processed_file_path, index=False)
            session['csv_file_path'] = processed_file_path
            return redirect(url_for('preview'))
        else:
            flash(error)
            return redirect(url_for('home'))

    flash('No file or valid Google Sheets URL provided.')
    return redirect(url_for('home'))

@app.route('/preview')
def preview():
    file_path = session.get('csv_file_path')
    if file_path and os.path.exists(file_path):
        data = pd.read_csv(file_path)
        csv_data = data.to_dict(orient='records')
        preview_data = data.head(10).to_dict(orient='records')
        return render_template('preview.html', csv_data=preview_data)
    flash("No data available for preview.")
    return redirect(url_for('home'))

# Function to extract the Google Sheet ID from the URL
def extract_sheet_id(sheet_url):
    pattern = r'/d/([a-zA-Z0-9_-]+)'
    match = re.search(pattern, sheet_url)
    print(pattern)
    return match.group(1) if match else None

@app.route('/authorize')
def authorize():
    # Redirect the user to Google's OAuth2 consent page
    authorization_url, state = flow.authorization_url(access_type='offline', include_granted_scopes='true')
    session['state'] = state
    return redirect(authorization_url)

@app.route('/oauth2callback')
def oauth2callback():
    # Ensure the state matches to prevent CSRF
    state = session.get('state')
    flow.fetch_token(authorization_response=request.url)

    # Save the credentials to the session
    credentials = flow.credentials
    session['credentials'] = credentials_to_dict(credentials)

    return redirect(url_for('fetch_google_sheet'))

@app.route('/fetch-google-sheet', methods=['POST','GET'])
def fetch_google_sheet():
    # Check if the user is entering a new Google Sheet URL
    if request.method == 'POST':
        sheet_url = request.form.get('sheet_url')  # Get the new Google Sheet URL from the form
        print(sheet_url)
        if not sheet_url:
            flash("Please provide a valid Google Sheet link.")
            return redirect(url_for('home'))

        # Store the new sheet URL in the session
        session['sheet_url'] = sheet_url
    else:
        # If it's a GET request, we use the previously stored sheet URL
        sheet_url = session.get('sheet_url')
        if not sheet_url:
            flash("No Google Sheet URL found. Please provide one.")
            return redirect(url_for('home'))
    print(sheet_url)
    if not sheet_url:
        print("sheet url not found")
        flash("Please provide a valid Google Sheet link.")
        return redirect(url_for('home'))

    # Extract the Sheet ID from the URL
    sheet_id = extract_sheet_id(sheet_url)
    print("extracted id", sheet_id)
    if not sheet_id:
        print("{e}")
        flash("Invalid Google Sheet URL. Please check and try again.")
        return redirect(url_for('home'))
    # Check if the user is authenticated
    if 'credentials' not in session:
        print("credential not in session")
        # Save the sheet URL in the session before redirecting for authentication
        sheet_url = request.args.get('sheet_url')  # Get URL from the query params (GET request)
        if sheet_url:
            session['sheet_url'] = sheet_url  # Store URL in session
        return redirect(url_for('authorize'))
    return googlesheet_preview(sheet_id)

def googlesheet_preview(sheet_id):
    # Load the credentials from the session
    credentials = Credentials(**session['credentials'])
    service = build('sheets', 'v4', credentials=credentials)
    range_name = 'Sheet1!A1:E20'  # Adjust as needed
    try:
        # Fetch data from Google Sheets
        sheet = service.spreadsheets()
        result = sheet.values().get(spreadsheetId=sheet_id, range=range_name).execute()
        values = result.get('values', [])
        # Convert the data to a pandas DataFrame for easy handling
        df = pd.DataFrame(values[1:], columns=values[0])  # Skip the header row
        preview_df = df.head(10)
        return render_template('sheetpreview.html', data=preview_df.to_html(index=False))
    except HttpError as err:
        # Handle errors related to Google Sheets API
        if err.resp.status == 403:
            flash("You don't have permission to access this Google Sheet. Please ensure it is shared with your account.")
            return redirect(url_for('home'))  # Redirect to home if permission error
        else:
            flash(f"Error fetching Google Sheets data: {err}")
        return redirect(url_for('home'))

def credentials_to_dict(credentials):
    return {
        'token': credentials.token,
        'refresh_token': credentials.refresh_token,
        'token_uri': credentials.token_uri,
        'client_id': credentials.client_id,
        'client_secret': credentials.client_secret,
        'scopes': credentials.scopes
    }

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('home'))

# Route to perform the search
@app.route('/perform-search', methods=['POST', 'GET'])
def perform_search():
    selected_column = request.form.get('selectedColumn')
    query = request.form.get('query')
    print(query)
    print(selected_column)
    if not selected_column or not query:
        flash("Please select a column and enter a search query.")
        print("Please select a column and enter a search query.")
        return redirect('/preview')

    # Load the processed CSV file path from the session
    file_path = session.get('csv_file_path')
    if not file_path or not os.path.exists(file_path):
        flash("Invalid CSV data or column.")
        print("Invalid CSV data or column.")
        return redirect('/home')
     # Read the CSV file into a DataFrame
    try:
        data = pd.read_csv(file_path)
    except Exception as e:
        flash(f"Error reading the uploaded file: {str(e)}")
        return redirect(url_for('home'))
     # Check if the selected column exists in the DataFrame
    if selected_column not in data.columns:
        flash(f"Invalid column: {selected_column}. Please select a valid column.")
        return redirect(url_for('preview'))
    # Extract non-empty values from the selected column
    column_data = data[selected_column].dropna().tolist()
    search_queries = [f"{value} {query}" for value in column_data]

    # Perform concurrent searches
    try:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        results = loop.run_until_complete(perform_concurrent_searches(search_queries))
    except Exception as e:
        flash(f"Error performing search: {str(e)}")
        return redirect(url_for('preview'))

    # Combine original values and search results
    results= [{'original': column_data[i], 'result': results[i]} for i in range(len(results))]


    return render_template('results.html', results=results)


# Async function to perform a single API call
async def fetch_result(session, query):
    params = {
        'engine': 'google',
        'q': query,
        'api_key': SERPAPI_API_KEY
    }
    async with session.get("https://serpapi.com/search", params=params) as response:
        if response.status == 200:
            data = await response.json()
            return data.get('organic_results', [{}])[0].get('snippet', 'No results found')
        else:
            return f"Error: {response.status}"

# Function to perform concurrent searches
async def perform_concurrent_searches(queries):
    async with aiohttp.ClientSession() as session:
        tasks = [fetch_result(session, query) for query in queries]
        return await asyncio.gather(*tasks)

# Results page
@app.route('/results')
def results_page():
    return render_template('results.html')
if __name__ == '__main__':
    app.run(debug=True)
