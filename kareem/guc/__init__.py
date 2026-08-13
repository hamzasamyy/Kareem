"""GUC (German University in Cairo) integration — logs into CMS, the
Student Portal, and Mail/OWA with the student's own credentials to find
assignment/quiz/final deadlines and surface them in Kareem's tracker and
Google Calendar. Isolated from the rest of Kareem: only kareem/trackers.py,
kareem/tools/calendar.py (Google Calendar), and kareem/safety.py patterns
are reused, nothing else is touched.
"""
