import requests as req
from patient import TRIALS_URL

def parse_form_to_dictionary(form):
    '''Takes in Flask Form object, returns a dictionary'''
    d = {}
    d['first_name'] = form['first_name']
    d['last_name'] = form['last_name']
    d['gender'] = form['gender']
    d['age'] = form['age']
    d['condition'] = form['condition']
    return d

test_patient_info = {"condition": "lung cancer"}

def get_all_trials():
    '''Returns trials data as list'''
    res = req.get(TRIALS_URL)
    return res.json()["trials"]
#print(get_all_trials())

def get_clinical_trials_briefs(patient_info):
    '''Takes in patient info as dict, returns matching clinical trials (brief title, brief summary)'''
    condition = patient_info["condition"]
    trials = get_all_trials()
    l = []
    for t in trials:
        for d in t["diseases"]:
            for s in d["synonyms"]:
                if s == condition:
                    l.append((t["brief_title"],t["brief_summary"]))
    return l

print(get_clinical_trials_briefs( test_patient_info))
