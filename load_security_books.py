"""Batch-load the Security-Books library into Amy's memory.

Downloads PDFs from github.com/mizazhaider-ceh/Security-Books and ingests
each one into Amy's RAG memory via nomic-embed-text embeddings.

Usage:
    python3 load_security_books.py              # load all books
    python3 load_security_books.py --list       # show what's already loaded
    python3 load_security_books.py --skip-existing  # skip already-ingested

Each book is downloaded to a temp file, embedded, then deleted.
Embeddings live in amy_memory.db — permanent, searchable, always available.
"""

import argparse
import os
import struct
import sys
import tempfile
import urllib.request
import urllib.parse

BASE_URL = "https://raw.githubusercontent.com/mizazhaider-ceh/Security-Books/main/"

# Full library — all PDF books in the repo
BOOKS = [
    "+100 Linux Commands - codelivly.pdf",
    "100 Security Operation Center Tools.pdf",
    "100 Web PenTesting checklist_💪🚨.pdf",
    "20210625-Red_Teaming_Handbook.pdf",
    "5 Steps to Get a Job in Cyber Security.pdf",
    "A Bug Hunter's Diary.pdf",
    "AI Tools in Cybersecurity – 2025 Edition.pdf",
    "AI in Cybersecurity.pdf",
    "API Security threats.pdf",
    "Advance XSS.pdf",
    "Advanced_Penetration_Testing_for_Highly.pdf",
    "Attacks on TCP.pdf",
    "Awesome Bugbounty Writeups.pdf",
    "Bash Shell Scripting for Pentesters_ Master the art of .pdf",
    "BashNotesForProfessionals.pdf",
    "Bash_Shell_Scripting_for_Pentesters_Master_the_art_of_command_line.pdf",
    "Bash_level1.pdf",
    "Bash_level2.pdf",
    "Bash_level3.pdf",
    "Becoming the Hacker.pdf",
    "Blue Team cheat Sheet.pdf",
    "Bug Bounty Bootcamp.pdf",
    "Bug Bounty Checklist for Web App.pdf",
    "Bug Bounty Hunting .pdf",
    "Bug Bounty Hunting Essentials.pdf",
    "Bug Bounty Methodology.pdf",
    "Bug Bounty PlayBook.pdf",
    "Bug Bounty Playbook (Ghostlulz) (Z-Library).pdf",
    "Bug Bounty Playbook V2v.pdf",
    "Bug Bounty Roadmap .pdf",
    "Bug Bounty Tools Collection.pdf",
    "Bug_Bounty_from_Scratch_A_comprehensive_guide_to_discovering_vulnerabilities.pdf",
    "Building an Application Security Program.pdf",
    "BurpSuite.pdf",
    "CEH_v03_Ethical_Hacking_&_Countermeasures_Web_Applications_&_Data.pdf",
    "CHFI_v01_Computer_Forensics_Investigation_Procedures_&_Response.pdf",
    "CHFI_v03_Computer_Forensics_Investigating_Data_&_Image_Files.pdf",
    "CISSP_For_Dummies-4E.pdf",
    "CPTS.pdf",
    "CTF Cheet Sheet.pdf",
    "CYBER SEC BASICS.pdf",
    "Certified Ethical Hacker (CEH) Study Guide.pdf",
    "Certified Ethical Hacker (CEH) v3.0 Official Course.pdf",
    "ChatGPT for CyberSecurity #1  ..pdf",
    "Cloud Penetration Testing for Red Teamers _ Learn How to.pdf",
    "Coding_for_Penetration_Testers_Building_Better_Tools_Jason_Andress.pdf",
    "Common Malware Types.pdf",
    "Common Ports.pdf",
    "Common Sense Guide to Mitigating Insider Threats, v7.pdf",
    "Complete Guide to CISM Certification.pdf",
    "Control System Defense_ Know the Opponent .pdf",
    "Creatively Malicious Prompt Engineering.pdf",
    "Cyber Security For CISO-CIO-CTO.pdf",
    "Cyber Security in the Age of Artificial Intelligence.pdf",
    "CyberCrime Espionage.pdf",
    "Cybersecurity_Strategies_and_Best_Practices_A_Comprehensive_Guide.pdf",
    "Ethical Hacking and Penetration Testing Guide (Rafay Baloch) (Z-Library).pdf",
    "Ethical Hacking with Python.pdf",
    "Google Hacking for Penetration Testers.pdf",
    "HANDS-ON PENETRATION TESTING WITH PYTHON.pdf",
    "Hacking Cryptography.pdf",
    "Hacking Kubernetes.pdf",
    "Hacking Project To Get Started.pdf",
    "Hacking_Secret_Ciphers_with_Python_A_beginners_guide_to_cryptography.pdf",
    "Hands_On_Ethical_Hacking_Tactics_Strategies,_tools,_and_techniques.pdf",
    "Infosec.pdf",
    "IntroToCrypto.pdf",
    "JavaScript_for_hackers_Learn_to_think_like_a_hacker_Heyes_Gareth.pdf",
    "Kali Linux For Hackers.pdf",
    "KaliAdvancedPentest.pdf",
    "KaliLinux.pdf",
    "KaliLinuxCTF.pdf",
    "KaliLinuxCookBook.pdf",
    "KaliLinuxRevealed.pdf",
    "KaliNetworkScanning.pdf",
    "KaliSocialEngineering.pdf",
    "Kali_Linux_2-_Windows_Penetration_Testing.pdf",
    "Kali_Linux_Revealed_Mastering_the_Penetration_Testing_Distribution.pdf",
    "Learn Ethical Hacking from Scratch.pdf",
    "Learn how to prevent phishing attacks.pdf",
    "Learn_Penetration_Testing_with_Python_3_x_An_ethical_hacker's_blueprint.pdf",
    "Linux Administration Cookbook.pdf",
    "Linux Basics for Hackers (Occupytheweb) (Z-Library).pdf",
    "Linux Networking Cookbook.pdf",
    "LinuxCommandLine.pdf",
    "Linux_Shell_Scripting_Cookbook,_Second_Edition.pdf",
    "MITRE ATTACKS.pdf",
    "Major Cybersecurity Focus Areas for 2026 .pdf",
    "Mastering Nmap Scripting.pdf",
    "McGraw_Hill_Gray_Hat_Hacking_4th_Edition_Jan_2015_ISBN_0071832386.pdf",
    "McGraw_Hill_Hacking_Exposed_Linux_3rd_Edition_Jul_2008_ISBN_0072262575.pdf",
    "McGraw_Hill_Hacking_Exposed_Network_Security_Secrets_And_Solutions.pdf",
    "Metasploit_The_Penetration_Testers_Guide_Kennedy,_David,_Jim_OGorman.pdf",
    "NIST Cyber Security Framework - Overview.pdf",
    "NIST Cyber Security Framework.pdf",
    "NIST Cybersecurity Framework in a nutshell !.pdf",
    "NIST Cybersecurity Framework.pdf",
    "NIST.CSWP.04162018.pdf",
    "Network Attacks and Exploitation.pdf",
    "Network Defense_ Fundamentals & Protocols.pdf",
    "Network Defense_ Perimeter Defense Mechanisms.pdf",
    "Network Defense_ Security & Vulnerability Assessment.pdf",
    "Network Defense_ Security Policy & Threats.pdf",
    "Network Performance and Security.pdf",
    "Network Safety Certification.pdf",
    "Network Security Through Data.pdf",
    "Network_Defense__Securing_&_Troubleshooting_Network_Operating_System.pdf",
    "Nmap-Cheat-Sheet.pdf",
    "OSCP Preparation Guide.pdf",
    "OSINT.pdf",
    "OWASP Top 10 Vulnerabilities -1.pdf",
    "OWASP Top 10 for  LLM Applications 2025.pdf",
    "OWASP Top 10 vulnerabilities .pdf",
    "Offensive Security Professional Overview Survival.pdf",
    "OpenAI API Cookbook _ Build Intelligent Applications.pdf",
    "PYTHON_FOR_OFFENSIVE_PENTEST_a_complete_practical_guide_to_Hussam.pdf",
    "Penetration Testing with Kali NetHunter.pdf",
    "Penetration_Testing_with_Kali_NetHunter_Gerald_\"Tripp\"_Roybal_III.pdf",
    "Pentest Handwriting Notes.pdf",
    "Pentesting With Burp Suite.pdf",
    "PowerShell for Penetration Testing.pdf",
    "PowerShell_for_Penetration_Testing_Explore_the_capabilities_of_PowerShell.pdf",
    "RANSOMWARE DEFENSE REPORT.pdf",
    "Real_World_Bug_Hunting_A_Field_Guide_to_Web_Hacking_Peter_Yaworski.pdf",
    "Roadmap to Becoming a Red Teamer.pdf",
    "SANS TOOLS.pdf",
    "Small Business Guide Cyber Security.pdf",
    "Subnetting.pdf",
    "The Psychology of Cybersecurity Hacking and the Human Mind.pdf",
    "The Ultimate Guide to Flag Hunting.pdf",
    "The_Hacker_Mindset_How_thinking_like_a_hacker_can_improve_your_code.pdf",
    "Top 100 Bugs.pdf",
    "Types Of Sensitive Information.pdf.pdf",
    "Ultimate_Penetration_Testing_with_Nmap_Master_Cybersecurity_Assessments.pdf",
    "Understanding_Network_Hacks_Attack_and_Defense_with_Python_Bastian.pdf",
    "Utilizing Generative AI for Cyber Defense Strategies.pdf",
    "Web Application Hacking.pdf",
    "Web Application Pen test.pdf",
    "Web Application Penetration Testing.pdf",
    "Web Hacking Arsenal (Rafay Baloch) (Z-Library).pdf",
    "Web Testing Security Guide.pdf",
    "What_Can_Generative_AI_Red_Teaming_Learn_from_Cyber_Red_Teaming.pdf",
    "Windows_and_Linux_Penetration_Testing_from_Scratch_Harness_the_power.pdf",
    "WordPress Pentesting Guide.pdf",
    "XSS Cheat Sheet.pdf",
    "ad pen test.pdf",
    "bug hunting guide.pdf",
    "cyber security prcaticle training.pdf",
    "cybersecurity career 2026.pdf",
    "linux-bible-by-christopher-negus.pdf",
    "net pen test.pdf",
    "windows event log analysis.pdf",
    "zero Trust Archi.pdf",
]


def _already_ingested(label):
    import sqlite3, os
    db_path = os.path.join(os.path.dirname(__file__), "amy_memory.db")
    if not os.path.exists(db_path):
        return False
    db = sqlite3.connect(db_path)
    cols = [r[1] for r in db.execute("PRAGMA table_info(mem)").fetchall()]
    if "source" not in cols:
        db.close()
        return False
    n = db.execute("SELECT COUNT(*) FROM mem WHERE source=?", (label,)).fetchone()[0]
    db.close()
    return n > 0


def download_and_ingest(filename, skip_existing=True):
    import learn_pdf as lp

    label = os.path.splitext(filename)[0]

    if skip_existing and _already_ingested(label):
        print(f"  [skip] already loaded: {label}")
        return 0

    encoded = urllib.parse.quote(filename)
    url = BASE_URL + encoded

    print(f"\n[↓] Downloading: {filename}")
    try:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp_path = tmp.name
        urllib.request.urlretrieve(url, tmp_path)
        size_kb = os.path.getsize(tmp_path) // 1024
        print(f"    Downloaded {size_kb}KB")

        if size_kb < 5:
            print(f"    [skip] file too small — likely not a readable PDF")
            os.unlink(tmp_path)
            return 0

        chunks = lp.ingest(tmp_path, label=label)
        os.unlink(tmp_path)
        return chunks

    except Exception as e:
        print(f"    [error] {e}")
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        return 0


def main():
    ap = argparse.ArgumentParser(description="Load security library into Amy's memory")
    ap.add_argument("--list", action="store_true", help="Show already-loaded books")
    ap.add_argument("--skip-existing", action="store_true", default=True,
                    help="Skip books already in memory (default: on)")
    ap.add_argument("--no-skip", action="store_true", help="Re-ingest everything")
    args = ap.parse_args()

    if args.list:
        import learn_pdf as lp
        lp.list_sources()
        return

    skip = not args.no_skip

    print(f"Security Books Library → Amy's memory")
    print(f"Books to process: {len(BOOKS)}")
    print(f"Skip existing: {skip}\n")

    total_chunks = 0
    loaded = 0
    failed = []

    for i, book in enumerate(BOOKS, 1):
        print(f"[{i}/{len(BOOKS)}]", end=" ")
        try:
            chunks = download_and_ingest(book, skip_existing=skip)
            if chunks:
                total_chunks += chunks
                loaded += 1
        except KeyboardInterrupt:
            print("\n\nInterrupted. Progress saved — run again to continue.")
            break
        except Exception as e:
            print(f"  [error] {book}: {e}")
            failed.append(book)

    print(f"\n{'='*60}")
    print(f"Done. {loaded} books loaded, {total_chunks:,} chunks embedded.")
    if failed:
        print(f"Failed ({len(failed)}): {', '.join(failed[:5])}{'...' if len(failed) > 5 else ''}")
    print("Amy knows the library. Ask her anything.")


if __name__ == "__main__":
    main()
