"""Prevent private HTML from being retained by browsers and shared proxies."""

from django.utils.cache import patch_cache_control, patch_vary_headers


class PrivateResponseMiddleware:
    """Cover error pages and redirects too; static assets bypass this middleware.

    Revoking album access must take effect on the next network request. Any
    future media cache must authorize the request before returning a 304.
    """

    def __init__(self, get_response):
        self.get_response = get_response

    def __call__(self, request):
        response = self.get_response(request)
        patch_cache_control(response, private=True, no_store=True)
        patch_vary_headers(response, ("Cookie",))
        response.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
        response.setdefault("Cross-Origin-Resource-Policy", "same-origin")
        response.setdefault("X-Robots-Tag", "noindex, nofollow, noarchive")
        if request.path_info.startswith("/s/"):
            response["Referrer-Policy"] = "no-referrer"
        return response
