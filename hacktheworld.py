import patient as pt
import logging
import sys
import umls
import requests as req
from typing import Dict, List, Optional, Union, Iterable, Match, Set, Callable, Type, cast, Tuple
from abc import ABCMeta, abstractmethod
from mypy_extensions import TypedDict 
from datetime import date
from distances import distance
from zipcode import Zipcode
from flask import current_app as app
from apis import VaApi, CmsApi, FhirApi, UmlsApi, NciApi
from fhir import Observation
from labtests import labs, LabTest
from datetime import datetime
import json
import re
import time
import tracemalloc

class Patient:
    def __init__(self, mrn: str, token: str):
        #logging.getLogger().setLevel(logging.DEBUG)
        self.mrn = mrn 
        self.token = token
        self.auth = umls.Authentication(app.config["UMLS_API_KEY"])
        self.tgt = self.auth.gettgt()
        self.api = VaApi(self.mrn, self.token)
        self.results: List[TestResult] = []
        self.latest_results: Dict[str, TestResult] = {}
        self.api = self.api_factory(self.mrn, self.token)
        self.umls = UmlsApi()
        self.nci = NciApi()
        self.conditions_by_code: Dict[str, Dict[str, str]] = {}
        self.no_matches: set = set()
        self.code_matches: Dict[str, Dict[str, str]] = {}
        self.trials_by_id: Dict[str, Trial] = {}
        self.trial_ids_by_ncit: Dict[str, List[str]] = {}
        # The following collections are to be deprecated:
        self.conditions: List[str]
        self.codes_ncit: List[Dict[str,str]] = []
        self.matches: List[Dict[str,str]] = []
        self.codes_without_matches: List[Dict[str, str]] = []
        self.trials: List[Trial] = []

        self.after_init()

    @abstractmethod
    def after_init(self) -> None:
        pass

    def load_demographics(self):
        self.gender, self.birthdate, self.name, self.zipcode, self.PatientJSON = pt.load_demographics(self.mrn, self.token)
        logging.debug("Patient gender: {}, birthdate: {}".format(self.gender, self.birthdate))

    def calculate_age(self):
        today = date.today()
        born = date.fromisoformat(self.birthdate)
        self.age = today.year - born.year - ((today.month, today.day) < (born.month, born.day))

    @abstractmethod
    def load_conditions(self) -> None:
        pass

    def load_codes(self):
        logging.info("loading Codes")

        for orig_code, match in self.umls.get_matches(self.conditions_by_code):
            if match:
                self.code_matches[orig_code] = match
            else:
                self.no_matches.add(orig_code)

        logging.info("Codes loaded - new approach")

        # Deprecate the following collections:
        self.codes_ncit = [{'ncit': match['match'], 'ncit_desc': match['description']} for match in self.code_matches.values()]
        self.matches = [{'orig_desc': self.conditions_by_code[orig_code]['description'], \
                        'orig_code': orig_code, \
                        'codeset': self.conditions_by_code[orig_code]['codeset'], \
                        'new_code': match['match'], \
                        'new_desc': match['description']} \
                            for orig_code, match in self.code_matches.items()]
        self.codes_without_matches = [{'orig_code': no_match, \
                                    'orig_desc': self.conditions_by_code[no_match]['description'], \
                                    'codeset': self.conditions_by_code[no_match]['codeset']} \
                                        for no_match in self.no_matches]

    def find_trials(self):
        logging.info("Searching for trials...")
        ncit_codes = {match['match'] for match in self.code_matches.values()}
        if len(ncit_codes) == 0:
            logging.info('No ncit conditions to search for')
            return
        for ncit_code in ncit_codes:
            self.trial_ids_by_ncit[ncit_code] = []
        for trial_json in self.nci.get_trials(self.age, self.gender, ncit_codes):
            logging.info(f"Processing trial {trial_json['nci_id']}, status: {trial_json.get('current_trial_status', '')}")
            diseases = trial_json['ncit_codes']
            trial = Trial(trial_json, list(diseases)[0] if len(diseases) > 0 else '')
            self.trials_by_id[trial.id] = trial
            for ncit_code in trial_json['ncit_codes']:
                self.trial_ids_by_ncit[ncit_code].append(trial.id)
        logging.info("Completed trials (new method)")

        # Deprecate the following collections:
        self.trials = list(self.trials_by_id.values())

        # self.trials: list = []
        # trials_json = pt.find_trials(self.codes_ncit, gender=self.gender, age=self.age)
        # for trialset in trials_json:
        #     code_ncit = trialset["code_ncit"]
        #     logging.debug("Trials for NCIT code {}:".format(code_ncit))
        #     for trial_json in trialset["trialset"]["trials"]:
        #         trial = Trial(trial_json, code_ncit)
        #         logging.debug("{} - {}".format(trial.id, trial.title))
        #         self.trials.append(trial)
        # logging.info("Trials found")
        for ncit_code in self.codes_ncit:
            print(ncit_code)
            new_trails_json = pt.find_new_trails(ncit_code)
            for trial_set in new_trails_json['FullStudiesResponse']['FullStudies']:
                print(trial_set['Study']['ProtocolSection'])
                trial = TrialV2(trial_set['Study']['ProtocolSection'], ncit_code['ncit'])
                self.trials.append(trial)
        print(self.conditions)
        print(self.matches)
        print(self.codes_ncit)

        return

    def load_all(self):
        self.load_demographics()
        self.calculate_age()
        self.load_conditions()
        self.load_codes()
        self.find_trials()
        return

class VAPatient(Patient):

    api_factory = VaApi

    def after_init(self):
        self.va_api: VaApi = cast(VaApi, self.api)
        # Deprecate the following collections:
        self.codes_snomed: List[str]

    def load_conditions(self):
        logging.info("Loading conditions")
        self.conditions_by_code = {condition.code: 
                                    {'codeset': condition.codeset, 'description': condition.description} 
                                        for condition in self.va_api.get_conditions()}

        # Deprecate the following collections:
        self.conditions = [cond['description'] for cond in self.conditions_by_code.values()]
        self.codes_snomed = list(self.conditions_by_code.keys())
        logging.info("Conditions loaded")

    def load_test_results(self) -> None:
        self.results = []
        for obs in self.api.get_observations():
            app.logger.debug(f"LOINC CODE = {obs.loinc}")
            result: Optional[TestResult] = TestResult.from_observation(obs)
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

    api_factory = CmsApi

    def load_conditions(self):
        for eob in self.cms_api.get_explanations_of_benefit():
            if eob.diagnoses:
                for diagnosis in eob.diagnoses:
                    code = diagnosis['code']
                    self.conditions_by_code[code if len(code)<4 else f"{code[0:3]}.{code[3:]}"] = {'codeset': diagnosis['codeset'], 'description': diagnosis['description']}

        # Deprecate the following collections:
        logging.info("CMS Conditions loaded")
        self.codes_icd9 = list(self.conditions_by_code.keys())
        self.conditions = [condition['description'] for condition in self.conditions_by_code.values()]
        logging.info("CMS condition collections computed")

class Criterion(TypedDict): 
    inclusion_indicator: bool
    description: str

class Trial:
    def __init__(self, trial_json, code_ncit):
        # self.trial_json = trial_json
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

class TrialV2(Trial):
    def __init__(self, trial_json, code_ncit): #write the code based on the condition it will attatch to that dropdown
        self.trial_json = trial_json
        self.code_ncit = code_ncit
        self.id = trial_json['IdentificationModule']['NCTId']
        self.title = trial_json['IdentificationModule']['BriefTitle']
        self.official = trial_json['IdentificationModule'].get('OfficialTitle')
        self.summary = trial_json['DescriptionModule'].get('BriefSummary')
        self.description = trial_json['DescriptionModule'].get('DetailedDescription')
        self.eligibility: List[Dict] = [{'description': trial_json['EligibilityModule'].get('EligibilityCriteria'), 'inclusion_indicator': True}]
        self.inclusions: List[str] = [criterion['description'] for criterion in self.eligibility if criterion['inclusion_indicator']]
        self.exclusions: List[str] = [criterion['description'] for criterion in self.eligibility if not criterion['inclusion_indicator']]
        self.measures = [measure for types in ['Primary', 'Secondary', 'Other'] for measure in self.get_measures(types)]
        self.pi = trial_json.get('SponsorCollaboratorsModule', {}).get('ResponsibleParty', {}).get('ResponsiblePartyInvestigatorFullName', 'N/A')
        self.sites = []
        self.population = trial_json['EligibilityModule'].get('StudyPopulation')
        self.diseases = []
        self.filter_condition = []

    def get_measures(self, key):
        return [
            {
                'name': measure.get(f'{key}OutcomeMeasure'),
                'description': measure.get(f'{key}OutcomeDescription'),
                'timeframe': measure.get(f'{key}OutcomeTimeFrame')
            }
                for measure in self.trial_json
                    .get('OutcomesModule', {})
                    .get(f'{key}OutcomeList', {})
                    .get(f'{key}Outcome', [])
            ]

class CombinedPatient:
    def __init__(self):
        self.VAPatient: Patient = None
        self.CMSPatient = None
        self.loaded = False
        self.clear_collections()
        self.numTrials = 0
        self.num_conditions_with_trials = 0
        self.filtered = False
        self.from_source: Dict = {}
    
    def clear_collections(self):
        self.trials: List[Trial] = []
        self.ncit_codes: list = []
        self.trials_by_ncit: list = []
        self.ncit_without_trials: list = []
        self.results: List[TestResult] = []
        self.latest_results: Dict[str, TestResult] = {}
        self.conditions_by_code: Dict[str, Dict[str, str]] = {}
        self.no_matches: set = set()
        self.code_matches: Dict[str, Dict[str, str]] = {}
        # Deprecate the following collections:
        self.matches: list = []
        self.codes_without_matches: list = []

    def calculate_distances(self):
        db = Zipcode()
        patzip = self.VAPatient.zipcode
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
        if self.CMSPatient is not None:
            self.append_patient_data(self.CMSPatient)
        if self.VAPatient is not None:
            self.append_patient_data(self.VAPatient)
            self.calculate_distances()
            self.results = self.VAPatient.results
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

    def append_patient_data(self,patient):
        patient.load_all()
        for trial in patient.trials:
            if not (trial in self.trials):
                self.trials.append(trial)
        for code in patient.codes_ncit:
            if not (code in self.ncit_codes):
                self.ncit_codes.append(code)

        self.conditions_by_code.update(patient.conditions_by_code)
        self.code_matches.update(patient.code_matches)
        self.no_matches.update(patient.no_matches)

        # Deprecate the following collections
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

