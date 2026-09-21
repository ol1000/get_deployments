import os
from flask import Flask, request, render_template_string
import requests
import json

app = Flask(__name__)

HTML_FORM = """
<!doctype html>
<title>New Relic Deployment Query</title>
<h2>New Relic Deployment Query</h2>
<form method=post>
  <label>API Key:</label><br>
  <input type=password name=api_key size=60><br><br>
  <label>Datacenter (US or EU):</label><br>
  <input type=text name=datacenter value="{{ datacenter|default('US') }}"><br><br>
  <input type=submit value="Query Deployments">
</form>

{% if error %}
  <p style="color:red;"><b>Error:</b> {{ error }}</p>
{% endif %}

{% if results %}
  <h3>Deployment Counts by Application and User</h3>
  <table border="1" cellpadding="5" cellspacing="0">
    <tr><th>Application</th><th>User</th><th>Count</th></tr>
    {% for app_user, count in results.items() %}
      <tr>
        <td>{{ app_user[0] }}</td>
        <td>{{ app_user[1] }}</td>
        <td>{{ count }}</td>
      </tr>
    {% endfor %}
  </table>
{% endif %}
"""

NEW_RELIC_ENDPOINTS = {
    "US": "https://api.newrelic.com/graphql",
    "EU": "https://api.eu.newrelic.com/graphql"
}

@app.route('/', methods=['GET', 'POST'])
def index():
    results = None
    error = None
    api_key = ""
    datacenter = "US"
    
    if request.method == 'POST':
        api_key = request.form.get('api_key', '').strip()
        datacenter = request.form.get('datacenter', 'US').upper()

        if not api_key:
            error = "API Key is required."
        elif datacenter not in NEW_RELIC_ENDPOINTS:
            error = "Datacenter must be 'US' or 'EU'."
        else:
            endpoint = NEW_RELIC_ENDPOINTS[datacenter]

            # Step 1: Get account IDs
            accounts_query = {
                "query": """
                {
                  actor {
                    organization {
                      accountManagement {
                        managedAccounts {
                          id
                        }
                      }
                    }
                  }
                }
                """
            }
            headers = {"Api-Key": api_key, "Content-Type": "application/json"}

            try:
                r = requests.post(endpoint, json=accounts_query, headers=headers)
                r.raise_for_status()
                resp_json = r.json()

                if 'errors' in resp_json:
                    error = "GraphQL errors: " + str(resp_json['errors'])
                else:
                    accounts = resp_json["data"]["actor"]["organization"]["accountManagement"]["managedAccounts"]
                    account_ids = [str(a["id"]) for a in accounts]
                    
                    if not account_ids:
                        error = "No managed accounts found for this user."
                    else:
                        # Step 2: Query Deployment data in batches (max 5 accounts per batch)
                        max_batch = 5
                        deployment_results = []

                        for i in range(0, len(account_ids), max_batch):
                            batch = account_ids[i:i+max_batch]
                            # Format batch as GraphQL list of integers without quotes
                            batch_str = "[" + ",".join(batch) + "]"

                            nrql_query = """
                            FROM Deployment SELECT entity.name, user SINCE 6 months ago LIMIT MAX
                            """
                            # Escape double quotes and newlines for embedding in GraphQL
                            nrql_escaped = nrql_query.replace('"', '\\"').replace('\n', ' ')

                            graphql_query = f'''
                            {{
                              actor {{
                                nrql(accounts: {batch_str}, query: "{nrql_escaped}", timeout: 60) {{
                                  results
                                }}
                              }}
                            }}
                            '''

                            resp = requests.post(endpoint, json={"query": graphql_query}, headers=headers)
                            resp.raise_for_status()
                            data = resp.json()
                            if "errors" in data:
                                error = f"GraphQL errors in batch query: {data['errors']}"
                                break
                            batch_results = data["data"]["actor"]["nrql"]["results"]
                            deployment_results.extend(batch_results)

                        if not error:
                            # Aggregate counts by (entity.name, user)
                            counts = {}
                            for row in deployment_results:
                                app_name = row.get("entity.name", "UNKNOWN")
                                user = row.get("user", "UNKNOWN")
                                counts[(app_name, user)] = counts.get((app_name, user), 0) + 1

                            results = counts

            except requests.exceptions.RequestException as e:
                error = f"Request failed: {e}"

    return render_template_string(HTML_FORM, results=results, error=error, datacenter=datacenter)



if __name__ == "__main__":
    debug_mode = os.environ.get("FLASK_DEBUG", "false").lower() == "true"
    app.run(host="0.0.0.0", port=5000, debug=debug_mode)
