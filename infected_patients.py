from __future__ import print_function
from time import sleep
import binascii
import os
import ndjson
import requests
from Crypto.Cipher import PKCS1_OAEP, AES
from Crypto.PublicKey import RSA
from Crypto.Hash import SHA256
from requests import request
from jsonpath_rw_ext import parse
from typing import Dict, List
from umls import Authentication

GCM_NONCE_SIZE = 12
GCM_TAG_SIZE = 16
EXPORT_URL = 'https://sandbox.bcda.cms.gov/api/v1/ExplanationOfBenefit/$export'
HEADERS = {
    'Authorization': "Bearer eyJhbGciOiJSUzUxMiIsInR5cCI6IkpXVCJ9.eyJhY28iOiJkM2U1MGEwNC0zMzA0LTRkMDEtYWUwMC1jNGNlOTIzODBkMTEiLCJleHAiOjE2MTYwODk3NTYsImlhdCI6MTU1MzAxNzc1NiwiaWQiOiJlNzFmMDQyYi1hMWZiLTQ0MDItYTgzZS1lZDlhYThlMTg4NmEiLCJzdWIiOiI1MDlkMjYwMy0wNzhiLTQ2YzgtODVmYi1lYzU3ZWEyY2QzYmQifQ.W2YKCNQNHkUukW1gP76cXr3J0JVeuYXSbgZQ9pAf2Rb_dnJ_GRn8g7rGwoGZUkCCv9fEOGUrbDpjpPJbNJUiwUqcfXCWkdQxTEfimAx5orC6UiNorjqPuKmloALWmN-B7b_62-BJ62u0xR5glbHl7CV5buI9yzWVzbMgvwuUH3VY3B7FQ-MXL3aqLtNoqlmfXjnARb4PsFBBReyJseIPpIbCQB3fUfV3DFL6wRWk4AW_Sa1w4UamzCyZER398cXE9CvpTylyVVdZSoP_p3V7tVyi5xVC8Jjf_zY3wJi2nc0ONqLqyMET8vfItVdYmJ6R4ShdIVRzDHFln7mXYVwOVw"
}
CLINICAL_TRIALS_URL = 'https://clinicaltrialsapi.cancer.gov/v1/clinical-trial/'


def decrypt_cipher(ct: 'File', key: str):
    nonce = ct.read(GCM_NONCE_SIZE)
    cipher = AES.new(key, AES.MODE_GCM, nonce=nonce, mac_len=GCM_TAG_SIZE)
    ciphertext = ct.read()
    return cipher.decrypt_and_verify(
        ciphertext[:-GCM_TAG_SIZE],
        ciphertext[-GCM_TAG_SIZE:]
    )


def decrypt(ek: str, filepath: str,  pk: str = 'ATO_private.pem'):
    encrypted_key = binascii.unhexlify(ek)
    with open(pk, 'r') as fh:
        private_key = RSA.importKey(fh.read())
    fh.close()
    base = os.path.basename(filepath)
    cipher = PKCS1_OAEP.new(key=private_key, hashAlgo=SHA256, label=base.encode('utf-8'))
    decrypted_key = cipher.decrypt(encrypted_key)

    with open(filepath, 'rb') as fh:
        result = decrypt_cipher(fh, decrypted_key).decode("utf-8")
    return result


def submit_get_patients_job() -> List:
    submit_header = {
        'Accept': "application/fhir+json",
        'Prefer': "respond-async"
    }
    submit_header.update(HEADERS)
    try:
        response = request("GET", EXPORT_URL, headers=submit_header)
        response.raise_for_status()
        job_url = response.headers['Content-Location']
        sleep(20)   # TODO async call
        job_response = request('GET', job_url, headers=HEADERS)
        if job_response.status_code == 200:
            job_response.raise_for_status()
            output = job_response.json().get('output', None)
            if output:
                patients = get_patients(output[0])
                print('done')
                return patients
    except Exception as exc:
        print(f'Failed due to : {exc}')
        raise Exception(exc)
    return []


def get_patients(body: Dict) -> List:
    encrypted_key = body['encryptedKey']
    patients_url = body['url']
    file_name = patients_url.split('/')[-1]
    response = request('GET', patients_url, headers=HEADERS)
    with open(file_name, 'wb') as f:
        f.write(response.content)
    nd_patients = decrypt(encrypted_key, file_name)
    patients = ndjson.loads(nd_patients)
    return patients


def get_infected_patients(patients: List[Dict], code: str = 'NCT02194738'):
    codes = get_diseases_icd_codes(code)
    codes.append('5672')    # TODO
    with open('outp.json') as f:
        patients = ndjson.load(f)
    parser = parse(f'$.resource.diagnosis[*].diagnosisCodeableConcept.coding[*].code')
    infected_patients = {}
    for patient in patients:
        patient_id = patient['resource']['patient']['reference']
        for match in parser.find(patient):
            if match.value in codes:
                infected_patients[patient_id] = patient['resource']
    return infected_patients


def get_nci_thesaurus_concept_ids(code: str):
    diseases = requests.get(CLINICAL_TRIALS_URL+code).json()['diseases']
    nci_thesaurus_concept_ids = [disease['nci_thesaurus_concept_id'] for disease in diseases]
    return nci_thesaurus_concept_ids


def get_diseases_icd_codes(code: str):
    auth = Authentication("97717581-9337-4572-8caf-2cee0ee461db")
    target = auth.gettgt()
    ticket = auth.getst(target)
    params = {'targetSource': 'ICD9CM', 'ticket': ticket}
    codeset = 'NCI'

    url = f'https://uts-ws.nlm.nih.gov/rest/crosswalk/current/source/{codeset}/'
    icd_codes = []
    for nci_thesaurus_concept_id in get_nci_thesaurus_concept_ids(code):
        res = requests.get(url + nci_thesaurus_concept_id, params=params)
        try:
            print(res.status_code)
            res.raise_for_status()
        except:
            continue
        for result in res.json()["result"]:
            if result["ui"] not in ("TCGA", "OMFAQ", "MPN-SAF"):
                code_ncit = result["ui"].replace('.','')
                icd_codes.append(code_ncit)
    return icd_codes
