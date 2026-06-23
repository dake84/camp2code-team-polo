

from dash import Dash, html

app = Dash(__name__)

app.layout = html.Div("Hallo, ich bin deine Dash-App!")

if __name__ == "__main__":
    app.run(debug=True, host="0.0.0.0")