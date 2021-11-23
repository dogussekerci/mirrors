#!/usr/bin/env python3
import json
from asyncio.exceptions import TimeoutError
import os
from logging import Logger

import requests
import yaml

from pathlib import Path
from typing import Optional, Union

from aiohttp import ClientSession, ClientError
from jsonschema import (
    ValidationError,
    validate,
)

from data_models import (
    MainConfig,
    RepoData,
    GeoLocationData,
    MirrorData,
)


# set User-Agent for python-requests
HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 6.1; WOW64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/56.0.2924.76 Safari/537.36',
    "Upgrade-Insecure-Requests": "1",
    "DNT": "1",
    "Accept": "text/html,application/xhtml+xml,"
              "application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
    "Accept-Encoding": "gzip, deflate"
}
# the list of mirrors which should be always available
WHITELIST_MIRRORS = (
    'repo.almalinux.org',
)
NUMBER_OF_PROCESSES_FOR_MIRRORS_CHECK = 15
AIOHTTP_TIMEOUT = 30


def load_json_schema(
        path: str,
) -> dict:
    """
    Load and return JSON schema from a file by path
    """
    with open(path, mode='r') as json_file:
        return json.load(json_file)


def config_validation(
        yaml_data: dict,
        json_schema: dict,
) -> tuple[bool, Optional[str]]:
    """
    Validate some YAML content by JSON schema
    """
    try:
        validate(
            instance=yaml_data,
            schema=json_schema,
        )
        return True, None
    except ValidationError as err:
        return False, err.message


def load_yaml(path: str):
    """
    Read and return content from a YAML file
    """
    with open(path, mode='r') as yaml_file:
        return yaml.safe_load(yaml_file)


def process_main_config(
    yaml_data: dict,
) -> tuple[Optional[MainConfig], Optional[str]]:
    """
    Process data of main config of the mirrors service
    :param yaml_data: YAML data from a file
    of main config of the mirrors service
    """

    def _process_repo_attributes(
            repo_name: str,
            repo_attributes: list[str],
            attributes: list[str],
    ) -> list[str]:
        for repo_arch in repo_attributes:
            if repo_arch not in attributes:
                raise ValidationError(
                    f'Attr "{repo_arch}" of repo "{repo_name}" is absent '
                    f'in the main list of attrs "{", ".join(attributes)}"'
                )
        return repo_attributes

    try:
        return MainConfig(
            allowed_outdate=yaml_data['allowed_outdate'],
            mirrors_dir=yaml_data['mirrors_dir'],
            versions=yaml_data['versions'],
            duplicated_versions=yaml_data['duplicated_versions'],
            arches=yaml_data['arches'],
            required_protocols=yaml_data['required_protocols'],
            repos=[
                RepoData(
                    name=repo['name'],
                    path=repo['path'],
                    arches=_process_repo_attributes(
                        repo_name=repo['name'],
                        repo_attributes=repo.get('arches', []),
                        attributes=yaml_data['arches']
                    ),
                    versions=_process_repo_attributes(
                        repo_name=repo['name'],
                        repo_attributes=[
                            str(ver) for ver in repo.get('versions', [])
                        ],
                        attributes=yaml_data['arches']
                    ),
                ) for repo in yaml_data['repos']
            ]
        ), None
    except ValidationError as err:
        return None, err.message


def get_config(
        logger: Logger,
        path_to_config: str = os.path.join(
            os.getenv('CONFIG_ROOT', '.'),
            'mirrors/updates/config.yml'
        ),
) -> Optional[MainConfig]:
    """
    Read, validate, parse and return main config of the mirrors service
    """

    path_to_json_schema = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        'json_schemas/service_config.json',
    )
    config_data = load_yaml(path=path_to_config)
    json_schema = load_json_schema(path=path_to_json_schema)
    is_valid, err_msg = config_validation(
        yaml_data=config_data,
        json_schema=json_schema,
    )
    if not is_valid:
        logger.error(
            'Main config of the mirror service is invalid because "%s"',
            err_msg,
        )
        return
    config, err_msg = process_main_config(yaml_data=config_data)
    if err_msg:
        logger.error(
            'Main config of the mirror service is invalid because "%s"',
            err_msg,
        )
        return
    return config


def process_mirror_config(
        yaml_data: dict,
        logger: Logger,
) -> MirrorData:
    """
    Process data of a mirror config
    :param yaml_data: YAML data from a file of a mirror config
    :param logger: instance of Logger class
    """

    def _get_mirror_subnets(
            subnets_field: Union[list, str],
            mirror_name: str,
    ):
        if isinstance(subnets_field, str):
            try:
                req = requests.get(subnets_field)
                req.raise_for_status()
                return req.json()
            except (requests.RequestException, json.JSONDecodeError) as err:
                logger.error(
                    'Cannot get subnets of mirror '
                    '"%s" by url "%s" because "%s"',
                    mirror_name,
                    subnets_field,
                    err,
                )
                return []
        return subnets_field

    return MirrorData(
        name=yaml_data['name'],
        update_frequency=yaml_data['update_frequency'],
        sponsor_name=yaml_data['sponsor'],
        sponsor_url=yaml_data['sponsor_url'],
        email=yaml_data.get('email', 'unknown'),
        urls={
            _type: url for _type, url in yaml_data['address'].items()
        },
        subnets=_get_mirror_subnets(
            subnets_field=yaml_data.get('subnets', []),
            mirror_name=yaml_data['name'],
        ),
        asn=yaml_data.get('asn'),
        cloud_type=yaml_data.get('cloud_type', ''),
        cloud_region=','.join(yaml_data),
        geolocation=GeoLocationData.load_from_json(
            yaml_data.get('geolocation', {}),
        ),
        private=yaml_data.get('private', False)
    )


def get_mirror_config(
        path_to_config: Path,
        logger: Logger,
) -> Optional[MirrorData]:
    """
    Read, validate, parse and return config of a mirror
    """
    path_to_json_schema = os.path.join(
        os.path.dirname(os.path.realpath(__file__)),
        'json_schemas/mirror_config.json',
    )
    mirror_data = load_yaml(path=str(path_to_config))
    json_schema = load_json_schema(path=path_to_json_schema)
    is_valid, err_msg = config_validation(
        yaml_data=mirror_data,
        json_schema=json_schema,
    )
    if not is_valid:
        logger.error(
            'Mirror config "%s" is invalid because "%s"',
            path_to_config.name,
            err_msg,
        )
        return
    config, err_msg = process_main_config(yaml_data=mirror_data)
    if err_msg:
        logger.error(
            'Mirror config "%s" is invalid because "%s"',
            path_to_config.name,
            err_msg,
        )
        return
    return config


def get_mirrors_info(
        mirrors_dir: str,
        logger: Logger,
) -> list[MirrorData]:
    """
    Extract info about all of mirrors from yaml files
    :param mirrors_dir: path to the directory which contains
           config files of mirrors
    :param logger: instance of Logger class
    """
    # global ALL_MIRROR_PROTOCOLS
    result = []
    for config_path in Path(mirrors_dir).rglob('*.yml'):
        mirror_info = get_mirror_config(
            path_to_config=config_path,
            logger=logger,
        )
        if mirror_info is not None:
            result.append(mirror_info)

    return result


async def mirror_available(
        mirror_info: MirrorData,
        versions: list[str],
        repos: list[RepoData],
        http_session: ClientSession,
        arches: list[str],
        required_protocols: list[str],
        logger: Logger,
) -> tuple[str, bool]:
    """
    Check mirror availability
    :param mirror_info: the dictionary which contains info about a mirror
                        (name, address, update frequency, sponsor info, email)
    :param versions: the list of versions which should be provided by a mirror
    :param repos: the list of repos which should be provided by a mirror
    :param arches: list of default arches which are supported by a mirror
    :param http_session: async HTTP session
    :param required_protocols: list of network protocols any of them
                               should be supported by a mirror
    :param logger: instance of Logger class
    """
    mirror_name = mirror_info.name
    logger.info('Checking mirror "%s"...', mirror_name)
    if mirror_info.private:
        logger.info(
            'Mirror "%s" is private and won\'t be checked',
            mirror_name,
        )
        return mirror_name, True
    try:
        urls = mirror_info.urls  # type: dict[str, str]
        mirror_url = next(
            address for protocol_type, address in urls.items()
            if protocol_type in required_protocols
        )
    except StopIteration:
        logger.error(
            'Mirror "%s" has no one address with protocols "%s"',
            mirror_name,
            required_protocols,
        )
        return mirror_name, False
    for version in versions:
        for repo_data in repos:
            arches = repo_data.arches or arches
            repo_path = repo_data.path.replace('$basearch', arches[0])
            check_url = os.path.join(
                mirror_url,
                str(version),
                repo_path,
                'repodata/repomd.xml',
            )
            try:
                async with http_session.get(
                        check_url,
                        headers=HEADERS,
                        timeout=AIOHTTP_TIMEOUT,
                ) as resp:
                    await resp.text()
                    if resp.status != 200:
                        # if mirror has no valid version/arch combos it is dead
                        logger.error(
                            'Mirror "%s" has one or more invalid repositories',
                            mirror_name
                        )
                        return mirror_name, False
            except (ClientError, TimeoutError) as err:
                # We want to unified error message so I used logging
                # level `error` instead logging level `exception`
                logger.error(
                    'Mirror "%s" is not available for version '
                    '"%s" and repo path "%s" because "%s"',
                    mirror_name,
                    version,
                    repo_path,
                    err,
                )
                return mirror_name, False
    logger.info(
        'Mirror "%s" is available',
        mirror_name,
    )
    return mirror_name, True