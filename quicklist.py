#!/usr/bin/python

import socket
import sys
import json
import queue
import time
import hashlib

from threading import (Thread, Lock)
from flask import (
    Blueprint, flash, g, redirect, render_template, request, session, url_for, current_app
)

from website.db import (get_db, get_db2)
from .forms import TickerForm

app = current_app

bp = Blueprint('quicklist', __name__, url_prefix='/quicklist')

# Create a UDS socket
sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)

# Connect the socket to the port where the server is listening
server_address = '/tmp/quicklist.sock'
print('connecting to {}'.format(server_address))
inQ = queue.Queue()
outQ = queue.Queue()
outHash = {}
completedJob = 0
reqID = 0
lock = Lock()

def nextReqID():
    global reqID

    lock.acquire()
    reqID += 1
    lock.release()
    return reqID

def incrementJob(origHash):
    global completedJob

    lock.acquire()
    if origHash in outHash:
        completedJob += 1
        outHash.pop(origHash)

    lock.release()

def doneJobs():
    return reqID == completedJob

try:
    sock.connect(server_address)
except socket.error as msg:
    print(msg)
    sys.exit(1)

def readSocket():
    while 1:
        size = 20000000

        data = sock.recv(size, socket.MSG_PEEK)
        eol = data.find(b'\n')

        if eol >= 0:
            size = eol + 1
        else:
            size = len(data)

        data = sock.recv(size)
        inQ.put(data.decode())

def writeSocket():
    entry = {"Event" : "SubscribeStream", "ReqID" : nextReqID() }
    sendRequest(json.dumps(entry), True)

    for msg in iter(outQ.get, None):
        print (msg)
        sock.sendall(msg)

def sendRequest(req, saveHash):
    if saveHash:
        hsh = hashlib.blake2b(digest_size = 24)
        hsh.update(req.encode())
        outHash[hsh.hexdigest()] = reqID

    outQ.put((req + "\n").encode())

def pingpong():
    while True:
        sendRequest("STATUS", False)
        time.sleep(10)

def readInQ():
    db = get_db2()

    for msg in iter(inQ.get, None):
        msg = msg.strip()

        processMsg(db, msg, -1)

def processMsg(db, msg, id):
    #print ("Processing " + msg)
    if (msg == "UP" or msg == "DOWN" or msg == "QUEUE"):
        db.execute('UPDATE qlserver SET status = ? WHERE host = "api.quicklist.tech"', (msg,))
        db.commit()
    else:
        msg = json.loads(msg)
        origHash = msg["OriginHash"]
        incrementJob(origHash)

        if msg["Event"] == "GetBulkTickers" or msg["Event"] == "GetTickers":
            processGetBulkTickers(db, msg)

        elif msg["Event"] == "GetBulkQuicklists" or msg["Event"] == "GetQuicklists":
            processGetBulkQuicklists(db, msg)

        elif msg["Event"] == "GetQuicklists":
            processGetQuicklists(db, msg)

        elif msg["Event"] == "CreateTicker" and msg["Errors"][0] == 0:
            processCreateTicker(db, msg, id)

        elif msg["Event"] == "DeleteTicker" and msg["Errors"][0] == 0:
            processDeleteTicker(db, msg, id)

        elif msg["Event"] == "CreateQuicklist" and msg["Errors"][0] == 0:
            processCreateQuicklist(db, msg, id)

        elif msg["Event"] == "UpdateQuicklist" and msg["Errors"][0] == 0:
            processUpdateQuicklist(db, msg, id)

        elif msg["Event"] == "DeleteQuicklist" and msg["Errors"][0] == 0:
            processDeleteQuicklist(db, msg, id)

        elif msg["Event"] == "GetOwners":
            processGetOwners(db, msg)
        elif msg["Event"] == "ReplayLog":
            i = 0

            for o in msg["ObjectArray"]:
                processMsg(db, json.dumps(o), i)

                i += 1
        #else:
        #    print ("")

def processGetOwners(db, msg):
    for o in msg["ObjectArray"]:
        row = db.execute("SELECT 1 FROM owners WHERE uuid = ?", (o["OwnerUUID"],) ).fetchone()

        if row is None:
            db.execute("INSERT INTO owners(uuid) VALUES(?)", (o["OwnerUUID"], ) )
            db.commit()

def processGetBulkTickers(db, msg):
    all = []

    for t in msg["ObjectArray"]:
        row = db.execute("SELECT 1 FROM tickers WHERE uuid = ?", (t["TickerUUID"],) ).fetchone()

        if row is None:
            db.execute("INSERT INTO tickers(uuid, quicklist, asset, exchange, symbol) VALUES(?, ?, ?, ?, ?)",
                                   (t["TickerUUID"], t["QuicklistUUID"], t["AssetClass"], t.get("Exchange", ""), t["Symbol"]), )
            db.commit()
        else:
            db.execute("UPDATE tickers SET quicklist = ?, asset = ?, exchange = ?, symbol = ? WHERE uuid = ? ",
                                   (t["QuicklistUUID"], t["AssetClass"], t.get("Exchange", ""), t["Symbol"], t["TickerUUID"]) )
            db.commit()

        all.append(t["TickerUUID"])

    localTickers = db.execute("SELECT * FROM tickers").fetchall()
    for t in localTickers:
        if t["uuid"] not in all:
            db.execute("DELETE FROM tickers WHERE uuid = ?", (t["uuid"],))
            db.commit()

def processGetBulkQuicklists(db, msg):
    all = []

    for ql in msg["ObjectArray"]:
        row = db.execute("SELECT 1 FROM quicklists WHERE uuid = ?", (ql["QuicklistUUID"],) ).fetchone()

        if row is None:
            db.execute("INSERT INTO quicklists(uuid, owner, name) VALUES(?, ?, ?)", (ql["QuicklistUUID"], ql["OwnerUUID"], ql["Name"]), )
            db.commit()
        else:
            db.execute("UPDATE quicklists SET name = ? WHERE uuid = ?", (ql["Name"], ql["QuicklistUUID"]))
            db.commit()

        all.append(ql["QuicklistUUID"])

    localLists = db.execute("SELECT * FROM quicklists").fetchall()
    for l in localLists:
        if l["uuid"] not in all:
            db.execute("DELETE FROM quicklists WHERE uuid = ?", (l["uuid"],))
            db.commit()

def updateSequence(db, msg):
    db.execute("UPDATE owners SET seq = ? WHERE uuid = ?", (msg["SeqID"], msg["BroadcastOwnerUUID"]))
    db.commit()

def processCreateTicker(db, msg, id):
    if not hasReloaded(db,msg,id):
        print(msg)

        uuid = msg["TickerUUID"]
        seq = msg["SeqID"]
        quicklist =  msg["QuicklistUUID"]
        symbol = msg["Object"]["Symbol"]
        ex = msg["Object"].get("Exchange", "")
        asset = msg["Object"]["AssetClass"]

        row = db.execute("SELECT 1 FROM tickers WHERE uuid = ?", (uuid,) ).fetchone()

        if row is None:
            db.execute("INSERT INTO tickers(uuid, quicklist, asset, exchange, symbol) VALUES(?, ?, ?, ?, ?)",
                                   (uuid, quicklist, asset, ex, symbol), )
            db.commit()

            updateSequence(db, msg)


def processDeleteTicker(db, msg, id):
    if not hasReloaded(db,msg,id):
        db.execute("DELETE FROM tickers WHERE uuid = ?", (msg["TickerUUID"],))
        db.commit()

        updateSequence(db, msg)

def processCreateQuicklist(db, msg, id):
    if not hasReloaded(db,msg,id):
        uuid = msg["QuicklistUUID"]
        owner = msg["BroadcastOwnerUUID"]
        seq = msg["SeqID"]
        name =  msg["Name"]

        row = db.execute("SELECT 1 FROM quicklists WHERE uuid = ?", (uuid,) ).fetchone()

        if row is None:
            db.execute("INSERT INTO quicklists(uuid, owner, name) VALUES(?, ?, ?)",
                                   (uuid, owner, name) )
            db.commit()

            updateSequence(db, msg)

def processUpdateQuicklist(db, msg, id):
    if not hasReloaded(db,msg,id):
        uuid = msg["QuicklistUUID"]
        owner = msg["BroadcastOwnerUUID"]
        seq = msg["SeqID"]
        name =  msg["Name"]

        db.execute("UPDATE quicklists SET name = ? WHERE uuid = ?",
                                   (name, uuid) )
        db.commit()

        updateSequence(db, msg)

def processDeleteQuicklist(db, msg, id):
    if not hasReloaded(db,msg,id):
        db.execute("DELETE FROM quicklists WHERE uuid = ?", (msg["QuicklistUUID"],))
        db.commit()

        updateSequence(db, msg)

def hasReloaded(db, msg, id):
    has = False

    if id == 0 or msg["SeqID"] == 1:
        has = reload(db, msg)

    return has

def reload(db, msg):
    row = db.execute("SELECT * FROM owners WHERE uuid = ? AND seq < ?", (msg["BroadcastOwnerUUID"], msg["SeqID"] - 1)).fetchone()

    if row is not None or msg["SeqID"] == 1:
        print("Reloading...")
        reloadAll(msg["BroadcastOwnerUUID"], msg["QuicklistUUID"])
        return True

    return False

def reloadAll(ownerUUID, quicklistUUID):
    sendRequest(json.dumps({"Event" : "GetOwners", "ReqID" : nextReqID() }), True)
    sendRequest(json.dumps({"Event" : "GetQuicklists", "OwnerUUID" : ownerUUID, "ReqID" : nextReqID() }), True)
    sendRequest(json.dumps({"Event" : "GetTickers", "OwnerUUID" : ownerUUID, "QuicklistUUID" : quicklistUUID,"ReqID" : nextReqID() }), True)


t1 = Thread(target=readSocket)
t1.start()

t2 = Thread(target=writeSocket)
t2.start()

t3 = Thread(target=readInQ)
t3.start()

t4 = Thread(target=pingpong)
t4.start()

@bp.route('/')
def index():
    db = get_db(app)
    form = TickerForm()

    server = db.execute('SELECT * FROM qlserver WHERE host = "api.quicklist.tech"').fetchone()
    stat = server["status"]

    qls = db.execute("SELECT * FROM quicklists ORDER BY name ASC").fetchall();
    args = request.args
    owner = ""
    ts = []

    if args.get("QuicklistUUID"):
        ql = db.execute("SELECT * FROM quicklists WHERE uuid = ? ORDER BY name ASC", (args.get("QuicklistUUID"),) ).fetchone()

        if ql:
            owner = ql["owner"]
            ts = db.execute("SELECT * FROM tickers WHERE quicklist = ?", (args.get("QuicklistUUID"),))

    if doneJobs():
        session["tickers"] = []
        session.clear()

    return render_template("index.html", status = stat, quicklists = qls, tickers = ts, args = args, form = form, owner = owner, temp = session.get("tickers", []))

@bp.route('/create_ticker', methods=['POST'])
def create_ticker():
    form = request.form

    if request.method == 'POST':
        if session.get("tickers") == None:
            session["tickers"] = []
        form = request.form

        entry = {"Event" : "CreateTicker", "ReqID" : nextReqID(), "QuicklistUUID" : form["quicklist"],
                 "Object" : {"Symbol" : form["symbol"], "Exchange" : form["exchange"], "AssetClass" : form["asset"]}, "OwnerUUID" : form["owner"] }

        tickers = session["tickers"]
        tickers.append(entry)
        session["tickers"] = tickers

        entry = json.dumps(entry)

        sendRequest(entry, True)

    return redirect("/quicklist?QuicklistUUID=" + form["quicklist"])

@bp.route('/delete_ticker/<id>', methods=['POST'])
def delete_ticker(id):
    if request.method == "POST":
        owner = request.form["owner"]
        reqID = nextReqID()

        entry = {"Event" : "DeleteTicker", "OwnerUUID" : owner, "ReqID" : reqID, "TickerUUID" : id}

        entry = json.dumps(entry)

        sendRequest(entry, True)

    return redirect("/quicklist?QuicklistUUID=" + request.form["quicklist"])

@bp.route("/stat")
def stat():
    sendRequest("STATUS", False)
    return "Sent STATUS"

@bp.route("/tickers")
def tickers():
    sendRequest(json.dumps({"Event" : "GetBulkTickers", "ReqID" : nextReqID() }), True)
    return "Sent GetBulkTickers"

@bp.route("/list")
def list():
    sendRequest(json.dumps({"Event" : "GetBulkQuicklists", "ReqID" : nextReqID() }), True)
    return "Sent GetBulkQuicklists"

@bp.route("/owners")
def owners():
    sendRequest(json.dumps({"Event" : "GetOwners", "ReqID" : nextReqID() }), True)
    return "Sent GetOwners"
