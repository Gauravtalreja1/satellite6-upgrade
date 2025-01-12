import sys
import time

import thread
from fabric.api import execute
from ovirtsdk.api import API
from ovirtsdk.infrastructure import errors
from ovirtsdk.xml import params

from upgrade.helpers import settings
from upgrade.helpers.logger import logger
from upgrade.helpers.tasks import capsule_sync
from upgrade.helpers.tasks import check_ntpd
from upgrade.helpers.tasks import check_settings_for_upgrade
from upgrade.helpers.tasks import foreman_service_restart


logger = logger()


def get_rhevm_client():
    """Creates and returns a client for rhevm.

    The following environment variables affect this command:

    RHEV_USER
        The username of a rhevm project to login.
    RHEV_PASSWD
        The password of a rhevm project to login.
    RHEV_URL
        An url to API of rhevm project.
    """
    username = settings.upgrade_old_infra.rhevm.rhev_user
    if username is None:
        logger.warning('The RHEV_USER environment variable should be defined.')
    password = settings.upgrade_old_infra.rhevm.rhev_passwd
    if password is None:
        logger.warning(
            'The RHEV_PASSWD environment variable should be defined.')
    api_url = settings.upgrade_old_infra.rhevm.rhev_url
    if api_url is None:
        logger.warning('An RHEV_URL environment variable should be defined.')
    try:
        return API(
            url=api_url,
            username=username,
            password=password,
            insecure=True
        )
    except errors.RequestError:
        logger.warning('Invalid Credentials provided for RHEVM.')
        sys.exit(1)


def create_rhevm_instance(instance_name, template_name, datacenter=None,
                          cluster=None, timeout=5):
    """Creates rhevm Instance from template.

    The assigning template should have network and storage configuration saved
    already.

    The following environment variables affect this command:

    RHEV_USER
        The username of a rhevm project to login.
    RHEV_PASSWD
        The password of a rhevm project to login.
    RHEV_URL
        An url to API of rhevm project.
    RHEV_DATACENTER
        Datacenter name in RHEV where instance is created
    RHEV_CLUSTER
        Cluster name in RHEV where instance is created

    :param instance_name: A string. RHEVM Instance name to create.
    :param template_name: A string. RHEVM image name from which instance
        to be created.
    :param datacenter: A string. Name of the datacenter in rhevm
    :param cluster: A string. Name of the cluster in rhevm
    :param int timeout: The polling timeout in minutes to create rhevm
    instance.
    """
    if not datacenter:
        datacenter = settings.upgrade_old_infra.rhevm.rhev_datacenter
    if not cluster:
        cluster = settings.upgrade_old_infra.rhevm.rhev_cluster
    rhevm_client = get_rhevm_client()
    template = rhevm_client.templates.get(name=template_name)
    datacenter = rhevm_client.datacenters.get(name=datacenter)
    logger.info('Turning on instance {0} from template {1}. Please wait '
                'till get up ...'.format(instance_name, template_name))
    rhevm_client.vms.add(
        params.VM(
            name=instance_name,
            cluster=rhevm_client.clusters.get(cluster),
            template=template))
    if wait_till_rhevm_instance_status(
            instance_name, 'down', timeout=timeout):
        rhevm_client.vms.get(name=instance_name).start()
        if wait_till_rhevm_instance_status(
                instance_name, 'up', timeout=timeout):
            logger.info('Instance {0} is now up !'.format(instance_name))
            # We can fetch the Instance FQDN only if RHEV-agent is installed.
            # Templates under SAT-QE datacenter includes RHEV-agents.
            if rhevm_client.datacenters.get(name='SAT-QE'):
                # get the hostname of instance
                vm_fqdn = rhevm_client.vms.get(
                    name=instance_name).get_guest_info().get_fqdn()
                logger.info('\t Instance FQDN : %s' % (vm_fqdn))
                # We need value of vm_fqdn so that we can use it with CI
                # For now, we are exporting it as a variable value
                # and source it to use via shell script
                file_path = "/tmp/rhev_instance.txt"
                with open(file_path, 'w') as f1:
                    f1.write('export SAT_INSTANCE_FQDN={0}'.format(vm_fqdn))
    rhevm_client.disconnect()


def delete_rhevm_instance(instance_name, timeout=5):
    """Deletes RHEVM Instance.

    The following environment variables affect this command:

    RHEV_USER
        The username of a rhevm project to login.
    RHEV_PASSWD
        The password of a rhevm project to login.
    RHEV_URL
        An url to API of rhevm project.

    :param instance_name: A string. RHEVM instance name to delete.
    :param int timeout: The polling timeout in minutes to delete rhevm
    instance.
    """
    rhevm_client = get_rhevm_client()
    vm = rhevm_client.vms.list(query='name={0}'.format(instance_name))
    if not vm:
        logger.info('The instance {0} is not found '
                    'in RHEV to delete!'.format(instance_name))
    else:
        logger.info('Deleting instance {0} from RHEVM.'.format(instance_name))
        if rhevm_client.vms.get(name=instance_name).get_delete_protected():
            logger.warning('The instance {0} is under delete protection and '
                           'cannot be deleted.'.format(instance_name))
            sys.exit(1)
        if rhevm_client.vms.get(
                name=instance_name).get_status().get_state() == 'up':
            rhevm_client.vms.get(name=instance_name).shutdown()
            if wait_till_rhevm_instance_status(instance_name, 'down'):
                rhevm_client.vms.get(name=instance_name).delete()
        elif rhevm_client.vms.get(
                name=instance_name).get_status().get_state() == 'down':
            rhevm_client.vms.get(name=instance_name).delete()
        timeup = time.time() + int(timeout) * 60
        while True:
            if time.time() > timeup:
                logger.warning(
                    'The timeout for deleting RHEVM instance has reached!')
                sys.exit(1)
            vm = rhevm_client.vms.list(query='name={0}'.format(instance_name))
            if not vm:
                logger.info('Instance {0} is now deleted from RHEVM!'.format(
                    instance_name))
                break
    rhevm_client.disconnect()


def wait_till_rhevm_instance_status(instance_name, status, timeout=5):
    """Waits untill given VM status reached.

    The following environment variables affect this command:

    RHEV_USER
        The username of a rhevm project to login.
    RHEV_PASSWD
        The password of a rhevm project to login.
    RHEV_URL
        An url to API of rhevm project.

    :param instance_name: A string. RHEVM Instance name to create.
    :param template_name: A string. RHEVM image name from which instance
        to be created.
    :param int timeout: The polling timeout in minutes to create rhevm
    instance.
    """
    rhevm_client = get_rhevm_client()
    timeup = time.time() + int(timeout) * 60
    while True:
        if time.time() > timeup:
            logger.warning(
                'Timeout in turning VM instance {0}.'.format(status))
            sys.exit(1)
        vm_status = rhevm_client.vms.get(
            name=instance_name).get_status().get_state()
        logger.info('Current Status: {0}'.format(vm_status))
        if vm_status == status:
            return True
        time.sleep(5)
    rhevm_client.disconnect()


def create_rhevm_template(host, cluster, new_template, storage):
    """ Creates template from Virtual machines

    :param string host: The Virtual machine name of which, template is to be
        created.
    :param string cluster: The Cluster name of the RHEVM, in which the
        template is to be created.
    :param string new_template: The name of the template to be created.
    :param string storage: The name of the storage domain, which will be
        used to create template.
    """
    get_client = get_rhevm_client()
    storage_domain = get_client.storagedomains.get(name=storage)
    size = storage_domain.get_available() / 1024 / 1024 / 1024
    vm = get_client.vms.get(host)
    if size > 300 and vm:
        try:
            vm.stop()
            logger.info('Waiting for VM to reach Down status')
            wait_till_rhevm_instance_status(host, 'down')
            logger.info('Template creation in Progress')
            get_client.templates.add(
                params.Template(name=new_template,
                                vm=get_client.vms.get(host),
                                cluster=get_client.clusters.get(cluster)))
            wait_till_rhevm_instance_status(host, 'down', timeout=80)
            if get_client.templates.get(new_template):
                logger.info('{0} template is created successfully'.format(new_template))
                get_client.disconnect()
        except Exception as ex:
            logger.error('Failed to Create Template from VM:\n%s' % str(ex))
            get_client.disconnect()
    else:
        get_client.disconnect()
        logger.error('Low Storage cannot proceed or VM not found')
        sys.exit()


# Fabric task
def validate_and_create_product_templates(product):
    """Task to do a sanity check on the satellite and capsule and then
    create their templates after z-stream upgrade

    Environment variables required to run upgrade on RHEVM Setup and will be
    fetched from Jenkins:
    ----------------------------------------------------------------------

    RHEV_SAT_HOST
        The rhevm satellite hostname to run upgrade on
    RHEV_CAP_HOST
        The rhevm capsule hostname to run upgrade on
    RHEV_STORAGE
        The storage domain on the rhevm used to create templates
    RHEV_CLUSTER
        Cluster name in RHEV where instance is created
    RHEV_SAT_IMAGE
        The satellite Image from which satellite instance will be created
    RHEV_CAP_IMAGE
        The capsule Image from which capsule instance will be created
    RHEV_SAT_INSTANCE
        The satellite instance name in rhevm of which template is to be
        created, generally the upgraded box
    RHEV_CAP_INSTANCE
        The capsule instance name in rhevm of which template is to be
        created, generally the upgraded box
    """
    # Get the instances name, specified in the jenkins job
    if product not in ['satellite', 'n-1']:
        os_version = settings.upgrade.os
        sat_instance = 'upgrade_satellite_auto_rhel{0}'.format(os_version)
        logger.info('Satellite Instance name {0}'.format(sat_instance))
        cap_instance = 'upgrade_capsule_auto_rhel{0}'.format(os_version)
        logger.info('Capsule Instance name {0}'.format(cap_instance))
        cluster = settings.upgrade_old_infra.rhevm.rhev_cluster
        storage = settings.upgrade_old_infra.rhevm.rhev_storage
        sat_host = settings.upgrade_old_infra.rhevm.rhev_sat_host
        new_sat_template = settings.upgrade_old_infra.rhevm.rhev_sat_image + "_new"
        cap_host = settings.upgrade_old_infra.rhevm.rhev_cap_host
        new_cap_template = settings.upgrade_old_infra.rhevm.rhev_cap_image + "_new"
        if check_settings_for_upgrade('capsule'):
            execute(check_ntpd, host=sat_host)
            execute(foreman_service_restart, host=sat_host)
            execute(capsule_sync, cap_host, host=sat_host)
            execute(check_ntpd, host=cap_host)
            execute(foreman_service_restart, host=cap_host)
            thread.start_new_thread(create_rhevm_template,
                                    (sat_instance,
                                     cluster,
                                     new_sat_template,
                                     storage
                                     ))
            thread.start_new_thread(create_rhevm_template,
                                    (cap_instance,
                                     cluster,
                                     new_cap_template,
                                     storage
                                     ))
            wait_till_rhevm_instance_status(sat_instance,
                                            'Image Locked',
                                            timeout=30
                                            )
            wait_till_rhevm_instance_status(sat_instance,
                                            'down',
                                            timeout=240)
