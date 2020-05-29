import patient as pt
import logging
import sys
import umls
import requests as req
from typing import Dict, List, Optional, Union, Iterable, Match, Set, Callable, Type, cast
from abc import ABCMeta, abstractmethod
from mypy_extensions import TypedDict 
from datetime import date
from distances import distance
from zipcode import Zipcode
from flask import current_app as app
from apis import VaApi, CmsApi, FhirApi
from fhir import Observation
from labtests import labs, LabTest
from datetime import datetime
import json
import re

class Patient(metaclass=ABCMeta):

    api_factory: Type[FhirApi]

    def __init__(self, mrn: str, token: str):
        #logging.geaLogger().setLevel(logging.DEBUG)
        self.mrn = mrn 
        self.token = token
        self.auth = umls.Authentication(app.config["UMLS_API_KEY"])
        self.tgt = self.auth.gettgt()
        self.results: List[TestResult] = []
        self.latest_results: Dict[str, TestResult] = {}
        self.api = self.api_factory(self.mrn, self.token)
        self.conditions: List[str]
        self.codes_snomed: List[str]
        self.after_init()

    def after_init(self):
        pass

    def load_demographics(self):
        dem = self.api.get_demographics()
        self.name = dem.fullname
        self.gender = dem.gender
        self.birthdate = dem.birth_date
        self.zipcode = dem.zipcode
        self.PatientJSON = dem.JSON
        logging.debug(f"Patient JSON: {self.PatientJSON}")
        logging.debug("Patient gender: {}, birthdate: {}".format(self.gender, self.birthdate))
        today = date.today()
        born = date.fromisoformat(self.birthdate)
        self.age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))

    @abstractmethod
    def load_conditions(self) -> None:
        pass
        # self.conditions,self.codes_snomed = pt.load_conditions(self.mrn, self.token)

    def load_codes(self):
        # self.codes, self.names = pt.find_all_codes(self.conditions)
        self.codes_ncit: list = []
        self.matches: list = []
        self.codes_without_matches: list = []
        for code_snomed in self.codes_snomed:
            code_ncit = self.snomed2ncit(code_snomed)
            orig_desc = self.conditions[self.codes_snomed.index(code_snomed)]
            if (code_ncit["ncit"] != "999999"):
                self.codes_ncit.append(code_ncit)
                self.matches.append({"orig_desc":orig_desc, "orig_code":code_snomed, "codeset":"SNOMED", "new_code":code_ncit["ncit"], "new_desc":code_ncit["ncit_desc"]})
            else:
                self.codes_without_matches.append({"orig_desc":orig_desc, "orig_code":code_snomed, "codeset":"SNOMED"})

    def find_trials(self):
        logging.info("Searching for trials...")
        self.trials: list = []
        #trials_json = pt.find_trials(self.codes)
        trials_json = pt.find_trials(self.codes_ncit, gender=self.gender, age=self.age)
        for trialset in trials_json:
            code_ncit = trialset["code_ncit"]
            logging.debug("Trials for NCIT code {}:".format(code_ncit))
            for trial_json in trialset["trialset"]["trials"]:
                trial = Trial(trial_json, code_ncit)
                logging.debug("{} - {}".format(trial.id, trial.title))
                self.trials.append(trial)
        return

    def load_all(self):
        self.load_conditions()
        self.load_codes()
        self.find_trials()
        return

    def print_trials(self):
        space = "      "
        for trial in self.trials: 
            print(trial.id)
            print(space + trial.title)
            print(space + trial.summary)
            print()
        return

    def code2ncit(self, code_orig, code_list, codeset):
        no_match = {"ncit": "999999", "ncit_desc": "No code match"}
        condition = self.conditions[code_list.index(code_orig)]
        tik = self.auth.getst(self.tgt)
        params = {"targetSource": "NCI", "ticket": tik}
        res = req.get(f'https://uts-ws.nlm.nih.gov/rest/crosswalk/current/source/{codeset}/{code_orig}', params=params)
        if (res.status_code != 200):
            logging.debug("{} CODE {} ({}) --> NO MATCH ({})".format(codeset, code_orig, condition, res.status_code))
            return no_match
        for result in res.json()["result"]:
            if not (result["ui"] in ["TCGA", "OMFAQ", "MPN-SAF"]): 
                name_ncit = result["name"]
                code_ncit = result["ui"]
                logging.debug("{} CODE {} ({})---> NCIT CODE {} ({})".format(codeset, code_orig, condition, code_ncit, name_ncit))
                logging.debug("{} CODE {} JSON: {}".format(codeset, code_orig, res.json()))
                return {"ncit": code_ncit, "ncit_desc": name_ncit}
        return no_match
    
    def snomed2ncit(self, code_snomed):
        return self.code2ncit(code_snomed, self.codes_snomed, "SNOMEDCT_US")

class VAPatient(Patient):

    api_factory = VaApi

    def after_init(self):
        self.va_api: VaApi = cast(VaApi, self.api)
        self.conditions_by_code: Dict[str, Dict[str, str]] = {}

    def load_conditions(self):
        self.conditions_by_code = {condition.code: 
                                    {'codeset': condition.codeset, 'description': condition.description} 
                                        for condition in self.va_api.get_conditions()}
        self.conditions = [cond['description'] for cond in self.conditions_by_code.values()]
        self.codes_snomed = list(self.conditions_by_code.keys())

    def load_test_results(self) -> None:
        self.results = []
        for obs in self.va_api.get_observations():
            app.logger.debug(f"LOINC CODE = {obs.loinc}")
            result = TestResult.from_observation(obs)
            if result is not None:
                app.logger.debug(f"Result added: {result.test_name} {result.value} {result.unit} on {result.datetime}")
                self.results.append(result)
                # Determine if result is the latest
                existing_result = self.latest_results.get(result.test_name)
                if existing_result is None or existing_result.datetime < result.datetime:
                    self.latest_results[result.test_name] = result

class CMSPatient(Patient):

    def after_init(self):
        self.cms_api: CmsApi = cast(CmsApi, self.api)
        self.conditions_by_code: Dict[str, Dict[str, str]] = {}

    api_factory = CmsApi

    def load_conditions(self):
        for eob in self.cms_api.get_explanations_of_benefits():
            for diagnosis in eob.diagnoses:
                code = diagnosis['code']
                self.conditions_by_code[code if len(code)<4 else f"{code[0:3]}.{code[3:]}"] = {'codeset': diagnosis['codeset'], 'description': diagnosis['description']}
        self.codes_icd9: list = []
        url = f"{app.config['CMS_API_BASE_URL']}ExplanationOfBenefit"
        params = {"patient": self.mrn, "_count":"50"}
        headers = {"Authorization": "Bearer {}".format(self.token)}
        res = req.get(url, params=params, headers=headers)
        fhir = res.json()
        # logging.debug("CONDITIONS JSON: {}".format(json.dumps(fhir, indent=2)))
        codes: list = []
        names: list = []
        for entry in fhir["entry"]:
            diags = entry["resource"]["diagnosis"]
            for diag in diags:
                coding = diag["diagnosisCodeableConcept"]["coding"][0]
                code = coding["code"]
                if len(code) > 3:
                    code = code[0:3] + "." + code[3:]
                if code != "999.9999" and not (code in codes) and "display" in coding:
                    codes.append(code)
                    names.append(coding["display"])
        self.codes_icd9 = codes
        self.conditions = names

    def load_codes(self):
        self.codes_ncit = []
        self.matches = []
        self.codes_without_matches = []
        for code_icd9 in self.codes_icd9:
            code_ncit = self.icd2ncit(code_icd9)
            orig_desc = self.conditions[self.codes_icd9.index(code_icd9)]
            if (code_ncit["ncit"] != "999999"):
                self.codes_ncit.append(code_ncit)
                self.matches.append({"orig_desc":orig_desc, "orig_code":code_icd9, "codeset":"ICD-9", "new_code":code_ncit["ncit"], "new_desc":code_ncit["ncit_desc"]})
            else:
                self.codes_without_matches.append({"orig_desc":orig_desc, "orig_code":code_icd9, "codeset":"ICD-9"})

    def icd2ncit(self, code_icd9):
        return self.code2ncit(code_icd9, self.codes_icd9, "ICD9CM")

class Criterion(TypedDict): 
    inclusion_indicator: bool
    description: str

class Trial:
    def __init__(self, trial_json, code_ncit):
        self.trial_json = trial_json
        self.code_ncit = code_ncit
        self.id = trial_json['nci_id']
        self.title = trial_json['brief_title']
        self.official = trial_json['official_title']
        self.summary = trial_json['brief_summary']
        self.description = trial_json['detail_description']
        self.eligibility: List[Criterion] = trial_json['eligibility']['unstructured']
        self.inclusions: List[str] = [criterion['description'] for criterion in self.eligibility if criterion['inclusion_indicator']]
        self.exclusions: List[str] = [criterion['description'] for criterion in self.eligibility if not criterion['inclusion_indicator']]
        self.measures = trial_json['outcome_measures']
        self.pi = trial_json['principal_investigator']
        self.sites = trial_json['sites']
        self.population = trial_json['study_population_description']
        self.diseases = trial_json['diseases']
        self.filter_condition: list = []

    def determine_filters(self) -> None:
        s: Set[str] = set()
        for text in self.inclusions:
            alias_match = labs.alias_regex.findall(text)
            if alias_match:
                criteria_match = labs.criteria_regex.findall(text)
                if criteria_match:
                    for group in criteria_match:
                        if labs.by_alias[group[1].lower()].name == "platelets":
                            s.add(group[4])
        for unit in s:
            app.logger.debug(f"leukocytes unit: {unit}")
                    
class CombinedPatient:

    patient_type: Dict[str, Type[Patient]] = {'va': VAPatient, 'cms': CMSPatient}
    
    def __init__(self):
        self.loaded = False
        self.clear_collections()
        self.numTrials = 0
        self.num_conditions_with_trials = 0
        self.filtered = False
        self.from_source: Dict[str, Patient] = {}

    def has_patients(self) -> bool:
        return len(self.from_source) > 0

    def va_patient(self) -> Optional[VAPatient]:
        patient = self.from_source.get('va', None)
        return patient if isinstance(patient,VAPatient) else None

    def login_patient(self, source: str, mrn: str, token: str):
        patient = self.patient_type[source](mrn, token)
        patient.load_demographics()
        self.from_source[source] = patient
        self.loaded = False

    def clear_collections(self):
        self.trials: List[Trial] = []
        self.ncit_codes: list = []
        self.trials_by_ncit: list = []
        self.ncit_without_trials: list = []
        self.matches: list = []
        self.codes_without_matches: list = []
        self.results: List[TestResult] = []
        self.latest_results: Dict[str, TestResult] = {}

    def calculate_distances(self):
        db = Zipcode()
        patzip = self.from_source['va'].zipcode
        pat_latlong = db.zip2geo(patzip)

        for trial in self.trials:
            for site in trial.sites:
                coordinates = site.get("org_coordinates", 0)
                if coordinates == 0:
                    site_latlong = db.zip2geo(site["org_postal_code"][:5])
                else:
                    site_latlong = (coordinates["lat"], coordinates["lon"])
                if (site_latlong is None) or (pat_latlong is None):
                    return
                site["distance"] = distance(pat_latlong, site_latlong)

    def load_data(self):
        self.clear_collections() 
        for source, patient in self.from_source.items():
            self.append_patient_data(patient)
            if source=='va':
                self.calculate_distances()
                self.results = patient.results
        for code in self.ncit_codes:
            trials = []
            for trial in self.trials:
                if trial.code_ncit == code["ncit"]:
                    trials.append(trial)
            if trials:
                self.trials_by_ncit.append({"ncit": code, "trials": trials})
            else:
                self.ncit_without_trials.append(code)
        self.loaded = True
        self.numTrials = len(self.trials)
        self.num_conditions_with_trials = len(self.trials_by_ncit)

    def load_test_results(self) -> None:
        va_patient = self.va_patient()
        if va_patient is None:
            return
        va_patient.load_test_results()
        self.results = va_patient.results
        self.latest_results = va_patient.latest_results

    def append_patient_data(self,patient):
        patient.load_all()
        for trial in patient.trials:
            if not (trial in self.trials):
                self.trials.append(trial)
        for code in patient.codes_ncit:
            if not (code in self.ncit_codes):
                self.ncit_codes.append(code)
        self.matches += patient.matches
        self.codes_without_matches += patient.codes_without_matches

class TestResult:

    def __init__(self, test_name: str, datetime: datetime, value: float, unit: str):
        self.test_name = test_name
        self.datetime = datetime
        self.value = value
        self.unit = unit

    @classmethod
    def from_observation(cls, obs: Observation) -> Optional['TestResult']:
        # Returns None if observation is not the result of a test we are tracking
        test: Optional[LabTest] = labs.by_loinc.get(obs.loinc)
        if test is not None and obs.datetime is not None and obs.value is not None and obs.unit is not None:
            return cls(test.name, obs.datetime, obs.value, obs.unit)
        else:
            return None

