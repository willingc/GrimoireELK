#!/usr/bin/python3
# -*- coding: utf-8 -*-
#
# Elastic Enrichment API
#
# Copyright (C) 2015 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Authors:
#   Alvaro del Castillo San Felix <acs@bitergia.com>
#


import logging

import requests

from dateutil import parser
from functools import lru_cache

logger = logging.getLogger(__name__)

try:
    import MySQLdb
    MYSQL_LIBS = True
except ImportError:
    logger.info("MySQL not available")
    MYSQL_LIBS = False

try:
    from sortinghat.db.database import Database
    from sortinghat import api
    from sortinghat.exceptions import AlreadyExistsError, NotFoundError, WrappedValueError
    SORTINGHAT_LIBS = True
except ImportError:
    logger.info("SortingHat not available")
    SORTINGHAT_LIBS = False


class Enrich(object):

    def __init__(self, db_sortinghat=None, db_projects_map=None, insecure=True):

        self.sortinghat = False
        if db_sortinghat and not SORTINGHAT_LIBS:
            raise RuntimeError("Sorting hat configured but libraries not available.")
        if db_sortinghat:
            self.sh_db = Database("root", "", db_sortinghat, "mariadb")
            self.sortinghat = True

        self.prjs_map = None
        if db_projects_map and not MYSQL_LIBS:
            raise RuntimeError("Projects configured but MySQL libraries not available.")
        if  db_projects_map:
            self.prjs_map = self._get_projects_map(db_projects_map)

        self.requests = requests.Session()
        if insecure:
            requests.packages.urllib3.disable_warnings(requests.packages.urllib3.exceptions.InsecureRequestWarning)
            self.requests.verify = False


    def _get_projects_map(self, db_projects_map):
        prjs_map = {}

        db = MySQLdb.connect(user="root", passwd="", host="mariadb",
                             db = db_projects_map)
        cursor = db.cursor()

        query = """
        SELECT data_source, p.id, pr.repository_name
        FROM projects p
        JOIN project_repositories pr ON p.project_id=pr.project_id
        """

        res = int(cursor.execute(query))
        if res > 0:
            rows = cursor.fetchall()
            for row in rows:
                [ds, name, repo] = row
                if ds not in prjs_map:
                    prjs_map[ds] = {}
                prjs_map[ds][repo] = name
        else:
            raise RuntimeError("Can't find projects mapping in %s" % (db_projects_map))
        return prjs_map

    def set_elastic(self, elastic):
        self.elastic = elastic

    def enrich_items(self, items):
        raise NotImplementedError

    def get_connector_name(self):
        """ Find the name for the current connector """
        from ..utils import get_connector_name
        return get_connector_name(type(self))

    def get_field_date(self):
        """ Field with the date in the JSON enriched items """
        raise NotImplementedError

    def get_fields_uuid(self):
        """ Fields with unique identities in the JSON enriched items """
        raise NotImplementedError

    def get_identities(self, item):
        """ Return the identities from an item """
        raise NotImplementedError

    def get_email_domain(self, email):
        domain = None
        try:
            domain = email.split("@")[1]
        except IndexError:
            # logging.warning("Bad email format: %s" % (identity['email']))
            pass
        return domain

    def get_identity_domain(self, identity):
        domain = None
        if identity['email']:
            domain = self.get_email_domain(identity['email'])
        return domain

    def get_item_id(self, eitem):
        """ Return the item_id linked to this enriched eitem """

        # If possible, enriched_item and item will have the same id
        return eitem["_id"]

    def get_last_update_from_es(self, _filter=None):

        last_update = self.elastic.get_last_date(self.get_field_date(), _filter)

        return last_update

    def get_elastic_mappings(self):
        """ Mappings for enriched indexes """

        mapping = '{}'

        return {"items":mapping}

    def get_grimoire_fields(self, creation_date, item_name):
        """ Return common grimoire fields for all data sources """

        grimoire_date = None
        try:
            grimoire_date = parser.parse(creation_date).isoformat()
        except Exception as ex:
            pass

        name = "is_"+self.get_connector_name()+"_"+item_name

        return {
            "grimoire_creation_date": grimoire_date,
            name: 1
        }

    # Project field enrichment

    def get_project_repository(self, item):
        """
            Get the repository name used for mapping to project name
            To be implemented for each data source
        """
        return ''

    def get_item_project(self, item):
        """ Get project mapping enrichment field """
        item_project = {}
        ds_name = self.get_connector_name()  # data source name in projects map
        repository = self.get_project_repository(item)
        try:
            project = (self.prjs_map[ds_name][repository])
            # logging.debug("Project FOUND for repository %s %s", repository, project)
        except KeyError:
            # logging.warning("Project not found for repository %s (data source: %s)", repository, ds_name)
            project = None
            # Try to use always the origin in any case
            if ds_name in self.prjs_map and item['origin'] in self.prjs_map[ds_name]:
                project = self.prjs_map[ds_name][item['origin']]
        item_project = {"project": project}
        # Time to add the project levels: eclipse.platform.releng.aggregator
        item_path = ''
        if project is not None:
            subprojects = project.split('.')
            for i in range(0, len(subprojects)):
                if i > 0:
                    item_path += "."
                item_path += subprojects[i]
                item_project['project_' + str(i+1)] = item_path
        return item_project

    # Sorting Hat stuff to be moved to SortingHat class

    def get_sh_identity(self, identity):
        """ Empty identity. Real implementation in each data source. """
        identity = {}
        for field in ['name', 'email', 'username']:
            identity[field] = None
        return identity

    def get_domain(self, identity):
        """ Get the domain from a SH identity """
        domain = None
        if identity['email']:
            try:
                domain = identity['email'].split("@")[1]
            except IndexError:
                # logging.warning("Bad email format: %s" % (identity['email']))
                pass
        return domain

    def is_bot(self, uuid):
        bot = False
        u = self.get_unique_identities(uuid)[0]
        if u.profile:
            bot = u.profile.is_bot
        return bot

    def get_enrollment(self, uuid, item_date):
        """ Get the enrollment for the uuid when the item was done """
        # item_date must be offset-naive (utc)
        if item_date and item_date.tzinfo:
            item_date = (item_date-item_date.utcoffset()).replace(tzinfo=None)

        enrollments = self.get_enrollments(uuid)
        enroll = 'Unknown'
        if len(enrollments) > 0:
            for enrollment in enrollments:
                if not item_date:
                    enroll = enrollment.organization.name
                    break
                elif item_date >= enrollment.start and item_date <= enrollment.end:
                    enroll = enrollment.organization.name
                    break
        return enroll

    def get_item_sh_fields(self, identity, item_date):
        """ Get standard SH fields from a SH identity """
        eitem = {}  # Item enriched

        eitem["author_uuid"] = self.get_uuid(identity, self.get_connector_name())
        # Always try to use first the data from SH
        identity_sh = self.get_identity_sh(eitem["author_uuid"])

        if identity_sh:
            eitem["author_name"] = identity_sh['name']
            eitem["author_user_name"] = identity_sh['username']
            eitem["author_domain"] = self.get_identity_domain(identity_sh)
        else:
            eitem["author_name"] = identity['name']
            eitem["author_user_name"] = identity['username']
            eitem["author_domain"] = self.get_identity_domain(identity)

        eitem["author_org_name"] = self.get_enrollment(eitem["author_uuid"], item_date)
        eitem["author_bot"] = self.is_bot(eitem['author_uuid'])
        return eitem

    def get_identity_sh(self, uuid):
        identity = {}

        u = self.get_unique_identities(uuid)[0]
        if u.profile:
            identity['name'] = u.profile.name
            identity['username'] = None
            identity['email'] = u.profile.email

        return identity

    def get_item_sh(self, item, identity_field):
        """ Add sorting hat enrichment fields for the author of the item """

        eitem = {}  # Item enriched
        if 'data' in item:
            # perceval data
            data = item['data']
        else:
            data = item

        # Add Sorting Hat fields
        if identity_field not in data:
            return eitem
        identity  = self.get_sh_identity(data[identity_field])
        eitem = self.get_item_sh_fields(identity, parser.parse(item[self.get_field_date()]))

        return eitem

    @lru_cache()
    def get_enrollments(self, uuid):
        return api.enrollments(self.sh_db, uuid)

    @lru_cache()
    def get_unique_identities(self, uuid):
        return api.unique_identities(self.sh_db, uuid)

    def get_uuid(self, identity, backend_name):
        """ Return the Sorting Hat uuid for an identity """
        # Convert the dict to tuple so it is hashable
        identity_tuple = tuple(identity.items())
        uuid = self.__get_uuid_cache(identity_tuple, backend_name)
        return uuid

    @lru_cache()
    def __get_uuid_cache(self, identity_tuple, backend_name):

        # Convert tuple to the original dict
        identity = dict((x, y) for x, y in identity_tuple)

        if not self.sortinghat:
            raise RuntimeError("Sorting Hat not active during enrich")

        iden = {}
        uuid = None

        for field in ['email', 'name', 'username']:
            iden[field] = None
            if field in identity:
                iden[field] = identity[field]

        try:
            # Find the uuid for a given id. A bit hacky in SH yet
            api.add_identity(self.sh_db, backend_name,
                             iden['email'], iden['name'],
                             iden['username'])
        except AlreadyExistsError as ex:
            uuid = ex.uuid
            u = api.unique_identities(self.sh_db, uuid)[0]
            uuid = u.uuid
        except WrappedValueError:
            logger.error("None Identity found")
            logger.error("%s %s" % (identity, uuid))
            uuid = None
        except NotFoundError:
            logger.error("Identity found in Sorting Hat which is not unique")
            logger.error("%s %s" % (identity, uuid))
            uuid = None
        except UnicodeEncodeError:
            logger.error("UnicodeEncodeError")
            logger.error("%s %s" % (identity, uuid))
            uuid = None
        except Exception as ex:
            logger.error("Unknown error adding sorting hat identity.")
            logger.error("%s %s" % (identity, uuid))
            uuid = None
        return uuid
