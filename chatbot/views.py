from django.shortcuts import render

# Create your views here.


"""
chatbot/views.py + urls (copy the urlpatterns into chatbot/urls.py)

Project urls.py:   path("api/chatbot/", include("chatbot.urls"))
Request  (POST, JWT header):  {"message": "total amount of invoices this month"}
"""
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from .engine import get_engine


class ChatbotView(APIView):
    permission_classes = [IsAuthenticated]

    def post(self, request):
        message = (request.data.get("message") or "").strip()
        if not message:
            return Response({"ok": False, "message": "message is required"}, status=400)
        if len(message) > 300:
            return Response({"ok": False, "message": "message too long"}, status=400)
        return Response(get_engine().answer(message, request.user))


# ---- chatbot/urls.py
# from django.urls import path
# from .views import ChatbotView
# urlpatterns = [path("", ChatbotView.as_view())]