from django.conf import settings
from django.http import HttpResponseRedirect, JsonResponse
from django.shortcuts import render


def home(request):
    return render(request, "home.html", {})


def redirect_awards(request):
    return HttpResponseRedirect("/tenders/awards/")


def redirect_map(request):
    """Published, machine-readable: what maps to what, forever."""
    return JsonResponse({"permanent": settings.LEGACY_REDIRECT_MAP, "policy": "Retired URLs redirect permanently; they never 404."})
