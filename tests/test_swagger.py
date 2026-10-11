from json import loads

from flask_testing import TestCase

from definitions import URL_PREFIX
from src.api.app import app


class TestOpenApiInfo(TestCase):
    """The published spec carries the legal pointers a client needs to find.

    termsOfService, contact and license are how an API consumer discovers the terms and the
    privacy contact without reading the frontend. They live in a flasgger template, which is
    merged into the generated spec, so the assertion is on the served JSON rather than on the
    template dict.
    """

    def create_app(self):
        app.config["TESTING"] = True
        return app

    def test_spec_info_names_terms_contact_and_license(self):
        response = self.client.get(f"{URL_PREFIX}/apispec_1.json")
        self.assert200(response)
        info = loads(response.data)["info"]
        self.assertEqual("AQRA", info["title"])
        self.assertTrue(info["termsOfService"].endswith("/terms"))
        self.assertTrue(info["contact"]["url"].endswith("/privacy"))
        self.assertEqual("Apache-2.0", info["license"]["name"])
        self.assertIn("OpenWeather", info["description"])

    def test_swagger_ui_loads_no_third_party_fonts(self):
        # flasgger's stock head.html pulls a Google Fonts stylesheet, which hands every
        # visitor's IP address to Google. src/api/config/templates/flasgger/head.html
        # overrides it; this fails if the override stops being picked up.
        response = self.client.get(f"{URL_PREFIX}/apidocs/")
        self.assert200(response)
        page = response.get_data(as_text=True)
        self.assertNotIn("fonts.googleapis.com", page)
        self.assertIn("swagger-ui", page)

    def test_swagger_ui_script_has_no_python_none(self):
        # flasgger renders config["auth"] verbatim into JavaScript; unset, it wrote
        # `let auth_config = None;` and the page threw a ReferenceError on load.
        page = self.client.get(f"{URL_PREFIX}/apidocs/").get_data(as_text=True)
        self.assertNotIn("auth_config = None", page)
        self.assertIn("auth_config = {}", page)
