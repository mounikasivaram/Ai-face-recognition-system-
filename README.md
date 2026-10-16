# AI Face Recognition

Local Flask/OpenCV demonstration with face enrollment, LBPH model training, face recognition, evaluation history, role-based sign-in, attendance check-in/out with date and time, a daily attendance home page, software-only access decisions, host-approved visitor passes with time windows, and CSV reports.

## First run

Open this folder in VS Code. In its terminal run:

```powershell
python -m pip install -r requirements.txt
python server.py
```

Open `http://127.0.0.1:5000/`. The first visit opens one-time Admin setup. Create a username and password of at least 8 characters. Passwords are stored as salted PBKDF2 hashes in the local SQLite database.

## Roles

- **Admin:** enroll/delete participants, train the model, create operator accounts, approve student/employee account requests, reset/delete accounts, set access permissions, recognize faces, manage attendance, and download project data backups.
- **Operator:** recognize faces, record attendance after a recognized result, and view reports and access attempts.
- **Student/Employee:** sign in to a personal attendance page and view only the attendance history linked to their approved enrolled profile. Hosts can approve or reject visitor requests assigned to their account.

The Admin creates Operator accounts from **User accounts**. Students and employees can select **Create an account** on the sign-in page, using the same full name and category as their enrolled face profile. Their request stays pending until the Admin verifies their school/work ID and approves the matching profile. Use **Log out** to end a session. The local user database is created automatically as `project_users.sqlite3`.

## Use the face-recognition demo

1. Sign in as Admin.
2. Enroll at least two participants with their permission. Enter each participant's name; it appears in the participant list, recognition results, and attendance records. Capture at least 3 readable face images per participant; 4 varied images are recommended.
3. Select **Train model** after adding images.
4. Sign in as an Operator or Admin and use **Recognize**.
5. For a recognized face, select **Check in** or **Check out**. The app confirms the person's name, event, date, and time. The Home page shows who is present or has not attended today, and the Attendance page lists timestamped records plus a month-filtered summary and CSV export.
6. Open **Access control**. New participants (students, employees, visitors, or security staff) start as **Not authorized**. An Admin can authorize or revoke any enrolled participant. Recognition then displays **Access granted** or **Access denied**, and records the attempt in the access log.
7. A student or employee can create an account at sign-in. After the Admin verifies the ID and approves the matching enrolled profile, that person can sign in to view their own attendance status, check-in date/time, and recent records. The attendance page refreshes automatically while it is open.
8. Enroll the guest with person type **Visitor**. In **Visitor passes**, select the guest, host account, visit purpose, and start/end time, then send the request. The host signs in to their member page and approves or rejects it; an Admin can also decide it. An approved pass displays a short code and allows the visitor recognition demo only within its time window. The Visitor passes page lists requests and their states.

The attendance action is available only for a face recognized in the prior two minutes in that session. Attendance status is an in-dashboard update, not an SMS or email. Access control and visitor pass decisions are software simulations for demonstration; they do not open a door or control a physical lock. Face recognition can make mistakes, so this is not suitable as a real security system. Keep face images and the local database private.


## Mobile access

The application can be opened from a phone over mobile data after it is deployed to a Python web host that provides a public HTTPS address. Browser camera access requires HTTPS. The local `127.0.0.1` address only works on the computer running the server. Before choosing a host, confirm how it stores uploaded face samples, trained models, user accounts, and attendance records. Temporary hosting can lose this data after a restart or redeploy. Do not upload participant face data without their permission.

This application is a prototype, not production-grade physical access control. Do not use its recognition decision as the only protection for a real entrance.
