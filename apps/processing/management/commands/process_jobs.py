import signal
import time

from django.core.management.base import BaseCommand, CommandError
from django.db import OperationalError, close_old_connections

from apps.processing.services import processing_options
from apps.processing.worker import claim_next_job, instance_lock, recover_expired, run_claimed_job


class Command(BaseCommand):
    help = "Valide les photos et génère les miniatures, un traitement à la fois."

    def add_arguments(self, parser):
        parser.add_argument("--once", action="store_true", help="Traiter au maximum un job puis sortir.")
        parser.add_argument("--poll-interval", type=float, default=5)

    def handle(self, *args, **options):
        if options["poll_interval"] < 0.1:
            raise CommandError("--poll-interval doit être supérieur ou égal à 0.1.")
        stopping = False

        def request_stop(signum, frame):
            nonlocal stopping
            stopping = True

        previous = {sig: signal.signal(sig, request_stop) for sig in (signal.SIGTERM, signal.SIGINT)}
        try:
            with instance_lock():
                self.stdout.write("Worker actif : un traitement simultané, enfants bornés en mémoire et durée.")
                while not stopping:
                    close_old_connections()
                    try:
                        if not processing_options()["paused"]:
                            recover_expired()
                            job = claim_next_job()
                            if job is not None:
                                run_claimed_job(job, lambda: stopping)
                                if options["once"]:
                                    break
                                continue
                    except OperationalError:
                        if options["once"]:
                            raise CommandError("La base est indisponible. Vérifier les migrations et les accès.") from None
                        self.stderr.write("Base indisponible ; nouvelle tentative après attente.")
                    if options["once"]:
                        break
                    deadline = time.monotonic() + options["poll_interval"]
                    while not stopping and time.monotonic() < deadline:
                        time.sleep(min(0.5, max(0, deadline - time.monotonic())))
        except RuntimeError as error:
            raise CommandError(str(error)) from None
        finally:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
