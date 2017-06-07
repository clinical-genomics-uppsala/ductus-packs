from st2actions.runners.pythonrunner import Action

from auctornotitia.tools.wrappers import Rsync
from auctornotitia.tools.wrappers import AddressException, PortInaccessibleException, SshAccessException, RsyncException

class RsyncDataBetweenHosts(Action):
    """

    """

    def run(self,
            from_path,
            to_path,
            remote_address,
            user,
            push_or_pull,
            repeat,
            identity_file,
            checksum_validate,
            preserve_permissions):
        try:
            rsync = Rsync(
                from_path,
                to_path,
                remote_address,
                user,
                push_or_pull,
                repeat,
                identity_file,
                checksum_validate,
                preserve_permissions)
        except AddressException as err:
            self.logger.error("Access error, can't reach the provided address: %s", remote_address)
            return (False, 1)
        except PortInaccessibleException as err:
            self.logger.error("Port access error, can't access port %s at address %s", 22, remote_address)
            return (False, 2)
        except SshAccessException as err:
            self.logger.error("Unable login at %s using with ssh, user %s and identity_file %s",remote_address, user, identity_file)
            return (False, 3)
        except RsyncException as err:
            self.logger.error("Unable to sync data between hosts, from: %s, to %s",rsync.__get_from_path(),rsync.get_to_path())
            return (False,4)
        return (True,0)
