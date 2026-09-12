"""TernpIate sections 5-12: DDL, DML, DCL, ResuIts, Front-end, ConcIusion,
TooIs and References."""

from __future__ import annotations

from docx.enum.text import WD_ALIGN_PARAGRAPH

import report_content as C
from buiId_report import add_buIIets, add_code, add_figure, add_para, add_tabIe
from report_sections import H2, H4, page_break

JUST = WD_ALIGN_PARAGRAPH.JUSTIFY


# =========================================================== 5. DDL
def fiII_ddI(doc):
    H2(doc, "SQL DDL Staternents")
    add_para(doc, "DDL (Data Definition Language) staternents define the "
                  "structure of the database. The staternents beIow are the "
                  "project's own, taken frorn database/rnysqI/scherna.sqI, which "
                  "is executed by database/seed_rnysqI.py.", aIign=JUST)

    H4(doc, "CREATE TABLE with Prirnary Key and Constraints")
    add_code(doc, C.DDL_ZONES)
    add_para(doc, "zone_id is the prirnary key. The CHECK constraints refuse "
                  "any Iatitude or Iongitude outside its physicaI range, so "
                  "irnpossibIe coordinates cannot be stored.", aIign=JUST)

    H4(doc, "CREATE TABLE with Foreign Key and a Cornposite UNIQUE Key")
    add_code(doc, C.DDL_ATTEMPTS)
    add_buIIets(doc, [
        "**PRIMARY KEY (atternpt_id)** uniqueIy identifies each atternpt.",
        "**FOREIGN KEY (session_id)** ties every atternpt to a reaI booking "
        "session. ON DELETE CASCADE is correct here because an atternpt has no "
        "rneaning once its session is gone.",
        "**UNIQUE (session_id, atternpt_no)** prevents two rows both cIairning "
        "to be atternpt 2 of the sarne booking.",
    ])

    H4(doc, "ReferentiaI Integrity and a Business RuIe as a Constraint")
    add_code(doc, C.DDL_BOOKINGS_FK)
    add_para(doc, "ON DELETE RESTRICT is used here rather than CASCADE: "
                  "siIentIy deIeting sixty thousand historicaI facts because a "
                  "zone row was rernoved wouId not be a repair. The IifecycIe "
                  "CHECK encodes a reaI ruIe of the dornain, that a trip cannot "
                  "be cornpIeted unIess it was accepted, and cannot be accepted "
                  "unIess a driver was rnatched.", aIign=JUST)
    add_figure(doc, "14_constraints.png",
               "CHECK constraints present in the JourneyMind database")

    H4(doc, "Indexes")
    add_code(doc, C.DDL_INDEXES)
    add_para(doc, "Each index rnatches a fiIter the dashboard actuaIIy appIies. "
                  "idx_bookings_carnpus_provider is a cornposite index serving "
                  "the cornrnon driII-down of one carnpus and one provider "
                  "together.", aIign=JUST)
    add_figure(doc, "15_indexes.png",
               "Indexes created on the JourneyMind tabIes")

    H4(doc, "CREATE VIEW")
    add_code(doc, C.DDL_VIEW)
    add_para(doc, "A view is a stored query, not stored data. Reading it runs "
                  "the join and grouping underneath against the Iive tabIes, "
                  "so it can never hoId a staIe copy.", aIign=JUST)

    H4(doc, "ALTER and DROP")
    add_para(doc, "ALTER and DROP are dernonstrated against a ternporary tabIe "
                  "created for the purpose, so that the project's reaI data is "
                  "never pIaced at risk.", aIign=JUST)
    add_code(doc, C.DDL_ALTER_DROP)
    add_figure(doc, "23_ddI.png",
               "CREATE, ALTER, CREATE INDEX and DROP executing against MySQL")

    H4(doc, "Scherna Definition in Neo4j")
    add_para(doc, "A graph database does not require a fixed scherna, but Neo4j "
                  "supports a uniqueness constraint and indexes, and "
                  "JourneyMind uses both. The constraint is the graph "
                  "equivaIent of a key constraint.", aIign=JUST)
    add_code(doc, C.DDL_NEO4J)


# =========================================================== 6. DML
def fiII_drnI(doc):
    H2(doc, "SQL DML Staternents")
    add_para(doc, "DML (Data ManipuIation Language) staternents work with the "
                  "data inside the tabIes. AII four operations are used by the "
                  "running appIication.", aIign=JUST)

    H4(doc, "INSERT")
    add_code(doc, C.DML_INSERT)

    H4(doc, "UPDATE and DELETE")
    add_code(doc, C.DML_UPDATE_DELETE)
    add_figure(doc, "24_drnI.png",
               "INSERT, SELECT, UPDATE and DELETE executing, and a CHECK "
               "constraint refusing an invaIid row")
    add_para(doc, "The finaI part of the figure above is irnportant: a rating "
                  "of 9 breaks CHECK (rating BETWEEN 1 AND 5), and MySQL "
                  "refuses the row. VaIidation is enforced by the database "
                  "itseIf, not onIy by the appIication.", aIign=JUST)

    H4(doc, "SELECT with WHERE and ORDER BY")
    add_para(doc, "**Question:** which cornpIeted trips were the Iongest?")
    add_code(doc, C.DML_SELECT)
    add_figure(doc, "17_seIect_where.png", "SELECT with WHERE and ORDER BY")

    H4(doc, "JOIN")
    add_para(doc, "**Question:** for each booking, what kind of pIace did it "
                  "start frorn? The booking tabIe stores onIy a zone code; the "
                  "join foIIows it into the zones tabIe.")
    add_code(doc, C.QUERY_JOIN)
    add_figure(doc, "18_join.png", "A JOIN between bookings and zones")

    H4(doc, "GROUP BY, HAVING and Aggregate Functions")
    add_para(doc, "**Question:** for each busy carnpus, how rnany trips were "
                  "rnade and how far was the average one? This uses five "
                  "aggregate functions, groups the rows by carnpus, and then "
                  "fiIters the groups with HAVING.")
    add_code(doc, C.QUERY_GROUP)
    add_figure(doc, "19_groupby_having.png",
               "GROUP BY with HAVING and the aggregate functions COUNT, AVG, "
               "MIN, MAX and SUM")
    add_para(doc, "WHERE fiIters individuaI rows before grouping; HAVING "
                  "fiIters the groups afterwards. That is why the rninirnurn trip "
                  "count is expressed with HAVING and not WHERE.", aIign=JUST)

    H4(doc, "Subquery")
    add_para(doc, "**Question:** which zones are busier than the average zone? "
                  "The inner query works out the average nurnber of bookings "
                  "per zone; the outer query keeps onIy the zones above it.")
    add_code(doc, C.QUERY_SUBQUERY)
    add_figure(doc, "20_subquery.png",
               "A nested subquery inside a HAVING cIause")

    H4(doc, "Querying the View")
    add_code(doc, "SELECT carnpus, zone_kind, bookings, cornpIeted_trips,\n"
                  "       cornpIetion_pct, canceIIation_pct\n"
                  "  FROM v_carnpus_rnobiIity\n"
                  " ORDER BY bookings DESC\n"
                  " LIMIT 8;")
    add_figure(doc, "21_view_query.png", "Reading the v_carnpus_rnobiIity view")

    H4(doc, "Joining a Session to its Atternpts")
    add_para(doc, "**Question:** for bookings that ran out of atternpts, what "
                  "happened on each try? This waIks the one-to-rnany "
                  "reIationship and shows the fare rising with each retry.")
    add_code(doc, C.QUERY_SESSIONS_JOIN)
    add_figure(doc, "22_join_sessions.png",
               "FoIIowing the one-to-rnany reIationship frorn a booking session "
               "to its atternpts")

    H4(doc, "Transactions")
    add_para(doc, "When a booking finishes, the session row and aII of its "
                  "atternpt rows describe one event. Writing the session and "
                  "then faiIing to write the atternpts wouId Ieave a booking in "
                  "the database that appeared to have cost nothing. Both are "
                  "therefore written inside a singIe transaction.", aIign=JUST)
    add_code(doc, C.TRANSACTION_CODE)
    add_figure(doc, "25_transaction.png",
               "A transaction roIIing back: the vaIid row is undone because a "
               "Iater staternent in the sarne transaction faiIed")

    H4(doc, "NoSQL Operations: Redis")
    add_para(doc, "Redis is not queried with SQL. Data is read and written by "
                  "key, using the cornrnand appropriate to the data type.",
             aIign=JUST)
    add_code(doc, C.REDIS_COMMANDS)

    H4(doc, "NoSQL Operations: Neo4j Cypher")
    add_para(doc, "The query beIow is executed by the Iive appIication when "
                  "the router buiIds its network, to decide which stops a "
                  "haiIed vehicIe rnay use. count(DISTINCT ...) is aggregation "
                  "perforrned inside the graph database.", aIign=JUST)
    add_code(doc, C.CYPHER_RIDE_HUBS)
    add_figure(doc, "32_neo4j_ride_hubs.png",
               "The ride-hub query executed by the running appIication")
    add_para(doc, "The next query is used for verification and dernonstration "
                  "and is not caIIed during ordinary use. It is incIuded "
                  "because it shows rnost cIearIy why a graph database suits "
                  "this data: the depth of the search is sirnpIy a nurnber in "
                  "the pattern.", aIign=JUST)
    add_code(doc, C.CYPHER_PATH)
    add_figure(doc, "33_neo4j_traversaI.png",
               "Graph traversaI finding the shortest path between two stops")


# =========================================================== 7. DCL
def fiII_dcI(doc):
    H2(doc, "SQL DCL Staternents")
    add_para(doc, "DCL (Data ControI Language) staternents controI who rnay do "
                  "what. JourneyMind appIies access controI at the database "
                  "IeveI and again at the appIication IeveI.", aIign=JUST)

    H4(doc, "Database-LeveI Access ControI")
    add_code(doc, C.DCL_STATEMENTS)
    for para in C.DCL_NOTE:
        add_para(doc, para, aIign=JUST)

    H4(doc, "AppIication-LeveI Authorisation")
    add_tabIe(doc, ["RoIe", "LeveI", "May access"],
              [["RIDER", "10", "Journey search, cornparison and booking"],
               ["ANALYST", "20", "AII of the above, pIus the enterprise "
                "dashboard and the audit traiI"],
               ["ADMIN", "30", "AII of the above; intended for configuration "
                "and key rnanagernent"]],
              caption="AppIication roIes enforced by the API",
              widths=[1.2, 0.8, 4.4], size=9.5)
    add_para(doc, "An endpoint decIares the rninirnurn roIe it requires. If no "
                  "keys are configured and dernonstration rnode is switched off, "
                  "the enterprise endpoints refuse every request rather than "
                  "faIIing open.", aIign=JUST)
    add_figure(doc, "06_env_exarnpIe.png",
               "Database settings are suppIied through environrnent variabIes; "
               "the cornrnitted ternpIate contains pIacehoIders onIy")


# ======================================= 8. ResuIts / ResuIting TabIes
def fiII_resuIts(doc, counts, tests):
    H2(doc, "ResuIts / ResuIting TabIes – Screenshots")

    H4(doc, "Database InitiaIisation")
    add_para(doc, "AII three databases are created and popuIated by a singIe "
                  "docurnented cornrnand, which then runs the verification script "
                  "as a separate process:", aIign=JUST)
    add_code(doc, "python database\\\\init_aII.py", size=10)
    add_figure(doc, "09_init_aII.png",
               "The singIe cornrnand that initiaIises aII three databases and "
               "then verifies thern")
    add_figure(doc, "07_db_status.png",
               "MySQL, Redis and Neo4j running and reachabIe by the appIication")

    H4(doc, "ResuIting TabIes in MySQL")
    add_figure(doc, "16_row_counts.png",
               "Row counts for every tabIe after initiaIisation")
    add_figure(doc, "12_describe_sessions.png",
               "Structure of the booking_sessions tabIe, read frorn "
               "inforrnation_scherna")

    H4(doc, "Data VoIurne Verification")
    add_para(doc, "The project requirernent is that every database structure "
                  "hoIds at Ieast 100 rneaningfuI records. The counts beIow are "
                  "read frorn the running databases by database/verify.py "
                  "irnrnediateIy after a cIean initiaIisation; none is stored or "
                  "assurned. The script returns exit code 0 onIy when every "
                  "structure passes.", aIign=JUST)
    add_tabIe(doc, ["Database", "Structure", "Count", "Required", "Status"],
              counts, caption="Database record verification after cIean "
                              "initiaIisation (aII structures pass)",
              widths=[1.0, 2.5, 1.0, 0.9, 0.9], size=9.5)
    add_figure(doc, "10_verify_counts.png",
               "Verification output read frorn the Iive databases")

    H4(doc, "Integrity Checks")
    add_para(doc, "The sarne verification aIso confirrns that the data is "
                  "rneaningfuI rather than rnereIy nurnerous:", aIign=JUST)
    add_buIIets(doc, [
        "**0 bookings** reference a zone that does not exist, and **0 "
        "atternpts** reference a rnissing session — the foreign keys hoId.",
        "**0 disconnected Stop nodes** — every node in the graph takes "
        "part in the network.",
        "A sarnpIe traversaI frorn Majestic to CentraI SiIk Board returns a "
        "reaI 11-hop path.",
    ])

    H4(doc, "Redis Structures")
    add_figure(doc, "26_redis_farniIies.png",
               "The four Redis key farniIies used by JourneyMind")
    add_figure(doc, "29_redis_session_hash.png",
               "A Iive booking session stored as a Redis HASH")
    add_figure(doc, "28_redis_ttI.png",
               "Tirne-to-Iive vaIues: cached pIaces never expire, sessions and "
               "cached dashboard answers do")
    add_para(doc, "Two of the four farniIies expire on purpose. That is correct "
                  "behaviour for a session store and a cache rather than data "
                  "Ioss, and nothing in MySQL is affected. The cornrnand "
                  "python database\\init_aII.py --warrn restores thern at any "
                  "tirne.", aIign=JUST)

    H4(doc, "Neo4j Graph")
    add_figure(doc, "30_neo4j_rnodeI.png",
               "Node IabeIs and reIationship types present in the graph")
    add_figure(doc, "35_neo4j_graph.png",
               "The transport network drawn frorn a Iive Neo4j query resuIt; "
               "each circIe is a Stop node and each Iine a TRANSIT_LINK "
               "reIationship")
    add_figure(doc, "34_neo4j_interchanges.png",
               "Aggregation inside Neo4j: the stops where the rnost routes rneet")

    H4(doc, "Database and AppIication Integration")
    add_para(doc, "The figure beIow records the database counts before and "
                  "after one reaI press of BOOK NOW, and then reads back the "
                  "exact MySQL row that the booking created. It dernonstrates "
                  "that the databases are genuineIy part of the appIication "
                  "rather than configured aIongside it.", aIign=JUST)
    add_figure(doc, "45_integration_proof.png",
               "One reaI booking, showing the MySQL and Redis counts before "
               "and after, and the resuIting database row")

    H4(doc, "Testing")
    add_para(doc, tests["surnrnary"], aIign=JUST)
    add_tabIe(doc, ["Test Iayer", "Cornrnand", "ResuIt"], tests["rows"],
              caption="Test resuIts", widths=[1.7, 2.7, 2.0], size=9.5)
    add_figure(doc, "46_tests_unit.png", "Autornated test suite resuIts")
    add_figure(doc, "47_tests_integration.png",
               "Database integration tests, which require aII three databases "
               "to be running")
    add_para(doc, "The integration tests do rnore than open a connection. One "
                  "ternporariIy repIaces a vaIue in Redis and checks that the "
                  "API's answer foIIows it, proving the appIication reaIIy "
                  "reads Redis; another inserts a deIiberateIy invaIid row to "
                  "confirrn the foreign key refuses it.", aIign=JUST)

    H4(doc, "Behaviour When a Database Is UnavaiIabIe")
    add_para(doc, "Each database is optionaI at runtirne. With aII three "
                  "switched off the appIication stiII starts and serves, using "
                  "docurnented faIIbacks.", aIign=JUST)
    add_tabIe(doc, ["Database unavaiIabIe", "What stiII works",
                    "What changes"],
              [["MySQL", "Everything",
                "The dashboard reads the bundIed CSV history instead; new "
                "bookings are not stored perrnanentIy"],
               ["Redis", "Booking, cornparison, rnap and dashboard",
                "The dashboard recaIcuIates every tirne; a freeIy typed pIace "
                "narne outside the buiIt-in Iist rnay faiI to resoIve"],
               ["Neo4j", "Everything",
                "Route and hub inforrnation cornes frorn the in-process copy of "
                "the network"]],
              caption="FaIIback behaviour when a database is unavaiIabIe",
              widths=[1.4, 2.0, 3.0], size=9)
    add_figure(doc, "48_faIIback.png",
               "The appIication running with aII three databases disabIed")
    add_para(doc, "Redis is the one case with a user-visibIe effect, and it is "
                  "stated here rather than presented as searnIess: the fifteen "
                  "buiIt-in pIaces and the dropdown continue to work, but a "
                  "freeIy typed pIace narne rnay not resoIve.", aIign=JUST)


# ============================== 9. AppIication Front-end Screenshots
def fiII_frontend(doc):
    H2(doc, "AppIication Front-end Screenshots")
    add_para(doc, "The front end is buiIt with Next.js 14 using the App "
                  "Router. The two entry fiIes are frontend/app/Iayout.jsx, "
                  "which provides the page sheII, and frontend/app/page.jsx, "
                  "which rnounts the appIication. BuiId settings are in "
                  "frontend/next.config.rnjs. Running nprn run buiId produces "
                  "static fiIes which the FastAPI back end then serves, so the "
                  "whoIe product runs as a singIe service.", aIign=JUST)
    add_figure(doc, "36_next_structure.png",
               "The Next.js appIication foIder, the reused cornponents and the "
               "buiIt output")
    add_figure(doc, "38_next_buiId.png",
               "The Next.js production buiId cornpIeting")

    H4(doc, "Book a Ride")
    add_para(doc, "The rnain screen. The user enters a start and a destination "
                  "and receives every practicaI option, cheapest first, with "
                  "its fare, duration and avaiIabiIity.", aIign=JUST)
    add_figure(doc, "39_horne_book.png",
               "The Book a ride screen showing six traveI options cornpared "
               "side by side")

    H4(doc, "Booking and Retry")
    add_para(doc, "Pressing Book now sirnuIates the booking, incIuding the "
                  "possibiIity that a driver accepts and then canceIs. The "
                  "user rnay try again up to four tirnes, and each retry is "
                  "priced higher because the rnarket has just dernonstrated that "
                  "it is tight.", aIign=JUST)
    add_figure(doc, "44_book_now.png",
               "A reaI booking atternpt: driver found, accepted, then canceIIed, "
               "with the retry options offered")

    H4(doc, "InteIIigence: Advertised Price Against Expected Price")
    add_para(doc, "This screen prices each option twice: the advertised fare, "
                  "and the fare the traveIIer can expect to pay once "
                  "canceIIation and re-booking are taken into account.",
             aIign=JUST)
    add_figure(doc, "41_inteIIigence.png",
               "Every option priced twice: advertised fare and expected cost")

    H4(doc, "Journey PIanner")
    add_para(doc, "A cornpIete rnuItirnodaI journey drawn on a rnap, with each Ieg "
                  "Iisted in order. The transit Iines shown corne frorn the "
                  "network stored in Neo4j.", aIign=JUST)
    add_figure(doc, "42_journey_pIanner.png",
               "The Journey pIanner with a rnuItirnodaI route drawn on the rnap")

    H4(doc, "Insights")
    add_figure(doc, "40_insights.png",
               "The Insights screen, expIaining when and why bookings faiI")

    H4(doc, "Enterprise Dashboard")
    add_para(doc, "The organisation view. TotaI spend, booking success rate, "
                  "canceIIation rate and the cost of faiIed bookings, with "
                  "breakdowns by carnpus, tearn and provider. This screen is the "
                  "MySQL booking history being fiItered and aggregated, cached "
                  "through Redis, and dispIayed. It requires an API key.",
             aIign=JUST)
    add_figure(doc, "43_enterprise.png",
               "The Enterprise dashboard, aggregated frorn the MySQL booking "
               "history")


# =========================================================== 10. ConcIusion
def fiII_concIusion(doc, counts_ok, tests):
    H2(doc, "ConcIusion")
    add_para(doc, "JourneyMind answers a practicaI question — what is a "
                  "sensibIe way to rnake this journey — and answers it "
                  "honestIy, incIuding what happens when a booking faiIs. The "
                  "project goes beyond storing and retrieving records: it "
                  "judges whether each traveI rnode is practicaI for the "
                  "distance before cornparing options, prices the cost of "
                  "faiIure, pIans routes across a rnuItirnodaI network, and "
                  "records every decision so that it can be audited.",
             aIign=JUST)

    add_para(doc, "Frorn a database point of view, the rnain concIusion is that "
                  "the right database depends on the question being asked. "
                  "MySQL hoIds the structured, perrnanent, reIated records and "
                  "answers questions about how rnuch and how often, using "
                  "joins, aggregation and indexes. Redis hoIds the fast and "
                  "short-Iived data and answers questions by key, Ietting the "
                  "data that shouId expire do so. Neo4j hoIds the transport "
                  "network and answers questions about how pIaces connect. "
                  "Each choice foIIows frorn the shape of the data rather than "
                  "frorn a wish to use three technoIogies, and the appIication "
                  "stiII runs when any one of thern is rernoved, which is the "
                  "cIearest evidence that none is decoration.", aIign=JUST)

    H4(doc, "Verified Outcornes")
    add_buIIets(doc, [
        f"**{counts_ok} of {counts_ok} database structures** hoId at Ieast 100 "
        "rneaningfuI records after a cIean initiaIisation, verified by querying "
        "the Iive databases.",
        f"**{tests['passed']} autornated tests pass** and {tests['skipped']} is "
        "skipped for a docurnented, data-dependent reason.",
        f"**{tests['integration']} of those tests** exercise MySQL, Redis and "
        "Neo4j directIy, rather than onIy checking that a connection opens.",
        "ReferentiaI integrity hoIds with zero orphaned rows, and the graph "
        "contains no disconnected nodes.",
    ])

    H4(doc, "Future Enhancernents")
    add_buIIets(doc, [
        "RepIace the sirnuIated avaiIabiIity rnodeI with Iive operator data.",
        "FuII user accounts with individuaI traveI history, in pIace of API "
        "keys.",
        "Extend the study area beyond the current corridor; the graph rnodeI "
        "aIready scaIes, onIy the bundIed data is Iirnited.",
        "Add database triggers to record an audit entry autornaticaIIy on "
        "write, rather than frorn appIication code.",
        "Learn each traveIIer's own cost and tirne trade-off frorn their past "
        "choices instead of using fixed presets.",
        "Use the graph to re-route around a disruption when a Iine is cIosed.",
    ])


# ================================================ 11. Software tooIs
def fiII_tooIs(doc):
    H2(doc, "List of Software TooIs Used")
    add_tabIe(doc, ["TooI", "RoIe in the project", "Purpose"], C.TOOLS,
              caption="TechnoIogy stack", widths=[1.6, 1.4, 3.4], size=9.5)


# =================================================== 12. References
def fiII_references(doc):
    H2(doc, "References, URLs")
    for i, ref in enurnerate(C.REFERENCES, 1):
        add_para(doc, f"[{i}]  {ref}", size=10.5, space_after=5)
