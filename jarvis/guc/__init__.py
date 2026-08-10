"""GUC (German University in Cairo) integration — logs into CMS, the
Student Portal, and Mail/OWA with the student's own credentials to find
assignment/quiz/final deadlines and surface them in Jarvis's tracker and
Google Calendar. Isolated from the rest of Jarvis: only jarvis/trackers.py,
jarvis/tools/calendar.py (Google Calendar), and jarvis/safety.py patterns
are reused, nothing else is touched.
"""
