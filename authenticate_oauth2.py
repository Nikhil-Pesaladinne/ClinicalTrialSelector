from flask import Flask, session, jsonify, redirect, render_template, request
from flask_session import Session
from flask_oauthlib.client import OAuth
from flask_bootstrap import Bootstrap
import hacktheworld as hack
import json

# creates the flask webserver and the secret key of the web server
app = Flask(__name__)
app.secret_key = "development" 

# runs the app with the OAuthentication protocol
SESSION_TYPE = 'filesystem'
app.config.from_object(__name__)
Session(app)
Bootstrap(app)
oauth = OAuth(app)

# specifies possible parameters for the protocol dealing with the CMS
cms = oauth.remote_app(
    'cms',
    base_url = "https://sandbox.bluebutton.cms.gov/v1/o",
    consumer_key = "vGeRleIRLnzQbGB8ylsCeUWCkG2oB32x0OjkLAzu",
    consumer_secret = "RkT9bH5vBKyITqNUnx70LUXjxvBOMHn1ORZDl87CApH7KKzbv6q9qmYbJoWJWK2q4LH5tJACdOYY0FuZA30yhLlukILcxa0CK9q2ICQiZEjSL2m7WZAUtcQiIPJqBgj3",
    request_token_params = {'scope': 'profile'},
    request_token_url = None,
    access_token_url = "https://sandbox.bluebutton.cms.gov/v1/o/token/",
    authorize_url = "https://sandbox.bluebutton.cms.gov/v1/o/authorize/",
    access_token_method = 'POST'
)

va = oauth.remote_app(
    'va',
    base_url="https://dev-api.vets.gov/",
    consumer_key="0oa3r2erccoQ1rRmg2p7",
    consumer_secret="nLp6CIis9u9wMcunPNzNa83hlU9eDMQmEyHAl-rq",
    request_token_params={
        'scope': 'openid offline_access profile email launch/patient veteran_status.read patient/Patient.read patient/Condition.read', "state": "12345"},
    request_token_url=None,
    access_token_url="https://dev-api.va.gov/oauth2/token/",
    authorize_url="https://dev-api.va.gov/oauth2/authorization/",
    access_token_method='POST'
)


def nl(line):
    return(line + "</br>")

def save_access_code(filename, mrn, token):
# creates a new file and gives permissions to write in it
# creates a dictionary with the medical record number and the token to enter into the file
# enters information in dictionary into file in json format
# saves and closes file
    fp = open(filename, 'w')
    acc = {"patient": mrn, "access_code": token}
    json.dump(acc, fp)
    fp.close()
    return

def authentications():
    auts = []
    if ('va_patient' in session): auts.append('va')
    if ('cms_patient' in session): auts.append('cms')
    return auts

def success_msg(filename, mrn, token):
# displays success message that shows file where credentials are stored, token, and mrn
# makes new line to show each credential
    html = nl("Success!")
    html += nl('')
    html += nl("Credentials stored in: " + filename)
    html += nl('')
    html += nl("Access token:")
    html += nl(token)
    html += nl('')
    html += nl("Patient ID:")
    html += nl(mrn)
    html += nl('')
    html += '<a href="/">Home</a>'
    return html


@app.route('/old')
# creates home page with links to authenticate with the VA and CMS
def home():
    auts = authentications()
    html = nl('Welcome!') + nl('')
    if ('va' in auts):
        html += nl('Your VA patient number is: ' +
                   session["va_patient"]) + nl('')
    else:
        html += nl('<button type="button" onclick="location.href = &quot;/va/authenticate&quot;;" id="VAButton2">Authenticate with VA ID.me</button>')
    if ('cms' in auts):
        html += nl('Your CMS patient number is: ' + session["cms_patient"]) + nl('')
    else:
        html += nl('<button type="button" onclick="location.href = &quot;/cms/authenticate&quot;;" id="CMSButton2">Authenticate with CMS</button>')

    if auts:
        if('trials' not in session):
            html += nl('<button type="button" onclick="location.href = &quot;/getInfo&quot;;" id="infoButton">Find Clinical Trials</button>')
        else:
            html += nl('<button type="button" onclick="location.href = &quot;/displayInfo&quot;;" id="infoButton">View Matched Clinical Trials</button>')
        html += nl('<button type="button" onclick="location.href = &quot;/logout&quot;;" id="logoutButton">Logout</button>')

    return html

@app.route('/')
def showtrials():
    return render_template('welcome.html')

@app.route('/cms/authenticate')
def cmsauthenticate():
    return cms.authorize(callback='http://localhost:5000/cmsredirect')

@app.route('/va/authenticate')
def vaauthenticate():
    return va.authorize(callback='http://localhost:5000/varedirect')

@app.route('/cmsredirect')
def cmsredirect():
    resp = cms.authorized_response()
    combined = session.get("combined_patient", hack.CombinedPatient())
    session['cms_access_token'] = resp['access_token']
    session['cms_patient'] = resp['patient']
    session.pop("trials", None)
    pat_token = {"mrn": resp["patient"], "token": resp["access_token"]}
    pat = hack.CMSPatient(resp['patient'], pat_token)
    pat.load_demographics()
    session['cms_gender'] = pat.gender
    session['cms_birthdate'] = pat.birthdate
    session['cms_name'] = pat.name
    combined.CMSPatient = pat
    combined.loaded = False
    session['combined_patient'] = combined
    return redirect('/cms/authenticated')

@app.route('/varedirect')
def varedirect():
    resp = va.authorized_response()
    combined = session.get("combined_patient", hack.CombinedPatient())
    session['va_access_token'] = resp['access_token']
    session['va_patient'] = resp['patient']
    session.pop("trials", None)
    pat_token = {"mrn": resp["patient"], "token": resp["access_token"]}
    pat = hack.Patient(resp['patient'], pat_token)
    pat.load_demographics()
    session['va_gender'] = pat.gender
    session['va_birthdate'] = pat.birthdate
    session['va_name'] = pat.name
    combined.VAPatient = pat
    combined.loaded = False
    session['combined_patient'] = combined
    return redirect('/va/authenticated')


@app.route('/cms/authenticated')
def cmsauthenticated():
    token = session.get('cms_access_token')
    mrn = session.get('cms_patient')
    filename = 'accesscodes/cms/' + mrn + '.json'
    save_access_code(filename, mrn, token)
    return redirect("/")


@app.route('/va/authenticated')
def vaauthenticated():
    token = session.get('va_access_token')
    mrn = session.get('va_patient')
    filename = 'accesscodes/va/' + mrn + '.json'
    save_access_code(filename, mrn, token)
    return redirect("/")

@app.route('/getInfo')
def getInfo():
    combined = session.get("combined_patient", hack.CombinedPatient())
    auts = authentications()
    if (not auts):
        return redirect("/")
    combined.load_data()

    session['codes'] = combined.ncit_codes
    session['trials'] = combined.trials
    session['numTrials'] = combined.numTrials
    session['index'] = 0
    session["combined_patient"] = combined
    return redirect("/")

@app.route('/trial')
def trial():
    return render_template('trial.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect("/")

@cms.tokengetter
def get_cms_token(token=None):
    return session.get('cms_access_token')


@va.tokengetter
def get_va_token(token=None):
    return session.get('va_access_token')
