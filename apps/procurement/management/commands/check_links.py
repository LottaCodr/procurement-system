"""Management command to verify link and redirect integrity.

Every URL must either return 200 or redirect to a URL that returns 200.
This prevents the Kano failure mode where all legacy URLs return 404.
"""
from django.core.management.base import BaseCommand, CommandError
from django.test import Client


class Command(BaseCommand):
    help = "Verify that all public URLs are accessible or properly redirect"

    def add_arguments(self, parser):
        parser.add_argument(
            "--verbose",
            action="store_true",
            help="Print every URL checked",
        )

    def handle(self, *args, **options):
        verbose = options.get("verbose", False)

        self.stdout.write("Checking link integrity...")

        client = Client()

        # List of URLs to check
        urls_to_check = [
            # Public pages
            "/",
            "/tenders/",
            "/tenders/awards/",
            "/tenders/contracts/",
            "/tenders/suppliers/",
            "/tenders/ratings/",
            "/tenders/payments/",
            "/tenders/register/",
            "/tenders/agent-desk/",
            "/tenders/status/",
            "/tenders/open-data/",
            "/tenders/indicators/",

            # API endpoints
            "/api/v1/stats",
            "/api/v1/suppliers",
            "/api/v1/releases",
            "/api/v1/ledger/head",
            "/api/v1/indicators",
            "/api/v1/policy/access",

            # Static
            "/stylesheet",
        ]

        # Add tender-specific URLs if tenders exist
        from procurement.models import Tender
        tenders = Tender.objects.filter(
            status__in=["PUBLISHED", "OPENED", "AWARDED", "CONTRACTED"]
        )[:5]

        for tender in tenders:
            urls_to_check.append(f"/tenders/{tender.ocid}/")
            urls_to_check.append(f"/api/v1/tenders/{tender.ocid}/ocds")

        errors = []
        checked = 0
        passed = 0

        for url in urls_to_check:
            checked += 1

            try:
                response = client.get(url, follow=False)

                if response.status_code == 200:
                    passed += 1
                    if verbose:
                        self.stdout.write(f"  ✓ {url} -> 200")

                elif response.status_code in [301, 302]:
                    # Follow redirect
                    redirect_url = response.url
                    response2 = client.get(redirect_url, follow=False)

                    if response2.status_code == 200:
                        passed += 1
                        if verbose:
                            self.stdout.write(
                                f"  ✓ {url} -> {response.status_code} -> "
                                f"{redirect_url} -> 200"
                            )
                    else:
                        errors.append(
                            f"{url} -> {response.status_code} -> "
                            f"{redirect_url} -> {response2.status_code}"
                        )
                        self.stderr.write(
                            self.style.ERROR(
                                f"  ✗ {url} -> {redirect_url} -> "
                                f"{response2.status_code}"
                            )
                        )

                else:
                    errors.append(f"{url} -> {response.status_code}")
                    self.stderr.write(
                        self.style.ERROR(f"  ✗ {url} -> {response.status_code}")
                    )

            except Exception as e:
                errors.append(f"{url} -> Exception: {e}")
                self.stderr.write(
                    self.style.ERROR(f"  ✗ {url} -> Exception: {e}")
                )

        # Summary
        self.stdout.write("")
        self.stdout.write(f"Checked {checked} URLs")
        self.stdout.write(f"Passed: {passed}")
        self.stdout.write(f"Failed: {len(errors)}")

        if errors:
            self.stderr.write("")
            self.stderr.write(self.style.ERROR("Failed URLs:"))
            for error in errors:
                self.stderr.write(self.style.ERROR(f"  {error}"))
            raise CommandError(f"{len(errors)} URLs failed")

        self.stdout.write(self.style.SUCCESS("✓ All URLs accessible"))
