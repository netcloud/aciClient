# -*- coding: utf-8 -*-
#
# MIT License
# Copyright (c) 2020 Netcloud AG

"""ACI

AciClient for doing Cert based RestCalls to the APIC

NOTES:
openssl req -new -newkey rsa:2048 -days 36500 -nodes -x509 -keyout apicUser.key -out apicUser.crt
"""
import logging
from OpenSSL import crypto
import base64
import requests
import json

# The modules are named different in python2/python3...
try:
    from urlparse import urlparse, urlunparse, parse_qsl
    from urllib import urlencode
except ImportError:
    from urllib.parse import urlparse, urlunparse, urlencode, parse_qsl

requests.packages.urllib3.disable_warnings()


class ACICert:
    __logger = logging.getLogger(__name__)

    # ==============================================================================
    # constructor
    # ==============================================================================
    def __init__(self, apicIp, pkPath, certDn):
        self.__logger.debug(f'Constructor called {apicIp} {pkPath} {certDn}')
        self.apicIp = apicIp
        self.baseUrl = 'https://' + self.apicIp + '/api/'
        self.__logger.debug(f'BaseUrl set to: {self.baseUrl}')
        self.pkey = crypto.load_privatekey(crypto.FILETYPE_PEM, open(pkPath, 'rb').read())
        self.certDn = certDn

    # ==============================================================================
    # packCookies
    # ==============================================================================
    def packCookies(self, content) -> {}:
        sigResult = base64.b64encode(crypto.sign(self.pkey, content, 'sha256')).decode()
        # print('sigResultType', type(sigResult), sigResult)
        return {'APIC-Certificate-Fingerprint': 'fingerprint',
                'APIC-Certificate-Algorithm': 'v1.0',
                'APIC-Certificate-DN': self.certDn,
                'APIC-Request-Signature': sigResult}

    # ==============================================================================
    # getJson
    # ==============================================================================
    def getJson(self, uri) -> {}:
        url = self.baseUrl + uri
        self.__logger.debug(f'Get Json called url: {url}')
        content = 'GET/api/' + uri
        cookies = self.packCookies(content)
        r = requests.get(url, cookies=cookies, verify=False)

        # Raise Exception if http Error occurred
        r.raise_for_status()

        self.__logger.debug(f'Successful get Data from APIC: {r.json()}')
        return r.json()['imdata']

    # ==============================================================================
    # getJson with Pagination
    # ==============================================================================
    def getJsonPaged(self, uri) -> {}:
        url = self.baseUrl + uri
        self.__logger.debug(f'Get Json Pagination called url: {url}')
        parsed_url = urlparse(url)
        parsed_query = parse_qsl(parsed_url.query)

        return_data = []
        page = 0

        while True:
            if page == 0:
                parsed_query.extend([('page-size', '50000'), ('page', page)])
            else:
                parsed_query[-1] = ('page', page)

            page += 1
            url_to_call = urlunparse((parsed_url[0], parsed_url[1], parsed_url[2], parsed_url[3],
                                      urlencode(parsed_query, safe="|"), parsed_url[5]))
            content = f'GET{parsed_url[2]}?{urlencode(parsed_query, safe="|")}'
            cookies = self.packCookies(content)
            r = requests.get(url_to_call, cookies=cookies, verify=False)

            # Raise Exception if http Error occurred
            r.raise_for_status()

            if r.ok:
                responseJson = r.json()
                self.__logger.debug(f'Successful get Data from APIC: {responseJson}')
                if responseJson['imdata']:
                    return_data.extend(responseJson['imdata'])
                else:
                    return return_data
    
    # ==============================================================================
    # postJson
    # ==============================================================================
    def postJson(self, jsonData):
        url = self.baseUrl + 'mo.json'
        self.__logger.debug(f'Post Json called data: {jsonData}')
        data = json.dumps(jsonData, sort_keys=True)
        content = 'POST/api/mo.json' + json.dumps(jsonData)
        cookies = self.packCookies(content)
        r = requests.post(url, data=data, cookies=cookies, verify=False)

        # Raise Exception if http Error occurred
        r.raise_for_status()

        if r.status_code == 200:
            self.__logger.debug(f'Successful Posted Data to APIC: {r.json()}')
            return r.status_code
        else:
            self.__logger.error(f'Error during get occured: {r.json()}')
            return r.status_code, r.json()['imdata'][0]['error']['attributes']['text']

    # ==============================================================================
    # deleteMo
    # ==============================================================================
    def deleteMo(self, dn):
        self.__logger.debug(f'Delete Mo called DN: {dn}')
        url = self.baseUrl + 'mo/' + dn + '.json'
        content = 'DELETE/api/mo/' + dn + '.json'
        cookies = self.packCookies(content)
        r = requests.delete(url, cookies=cookies, verify=False)

        # Raise Exception if http Error occurred
        r.raise_for_status()

        return r.status_code
