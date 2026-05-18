from weasyprint import HTML

html_content = """
<!DOCTYPE html>
<html lang="en">
<head>
    <meta charset="UTF-8">
    <style>
        @page {
            size: A4;
            margin: 15mm;
        }
        body {
            font-family: Arial, Helvetica, sans-serif;
            line-height: 1.4;
            color: #333;
            margin: 0;
            padding: 0;
        }
        .header {
            text-align: center;
            border-bottom: 2px solid #2c3e50;
            padding-bottom: 10px;
            margin-bottom: 15px;
        }
        .name {
            font-size: 20pt;
            font-weight: bold;
            color: #2c3e50;
            text-transform: uppercase;
            margin: 0;
        }
        .title {
            font-size: 12pt;
            color: #555;
            margin: 5px 0;
        }
        .contact {
            font-size: 9pt;
            color: #666;
        }
        .section {
            margin-bottom: 12px;
        }
        .section-title {
            font-size: 13pt;
            font-weight: bold;
            color: #2c3e50;
            border-bottom: 1px solid #ddd;
            margin-bottom: 8px;
            text-transform: uppercase;
        }

        /* ── ATS FIX: replaced flex + float with a plain table ── */
        .item-header {
            width: 100%;
            border-collapse: collapse;
            margin-top: 5px;
        }
        .item-header td {
            padding: 0;
            font-weight: bold;
            font-size: 11pt;
            vertical-align: middle;
        }
        .item-header .date {
            text-align: right;
            font-weight: normal;
            font-style: italic;
            font-size: 10pt;
            white-space: nowrap;
        }
        /* ────────────────────────────────────────────────────── */

        .item-subheader {
            font-style: italic;
            font-size: 10pt;
            color: #555;
        }
        ul {
            margin: 5px 0;
            padding-left: 20px;
        }
        li {
            font-size: 10pt;
            margin-bottom: 3px;
            text-align: justify;
        }
        .skills-container {
            font-size: 10pt;
        }
        .skill-category {
            font-weight: bold;
        }
    </style>
</head>
<body>
    <div class="header">
        <h1 class="name">MOHAMED ABD ELRHMAN AZZAM</h1>
        <p class="title">AI & Data Governance Engineer | Working-Student Candidate</p>
        <p class="contact">
            Erlangen, Germany | mohammed.abd.elrhman.azzam@gmail.com | +49 152 5617 2336<br>
            <a href="https://linkedin.com/in/mohammed-azzam">LinkedIn</a> |
            <a href="https://github.com/mohammed-azzam">GitHub</a>
        </p>
    </div>

    <div class="section">
        <h2 class="section-title">Objective</h2>
        <p style="font-size: 10pt; margin: 0;">Master's student in Autonomy Technologies at FAU Erlangen-Nürnberg with hands-on experience in custom CRM development, data engineering, and AI-driven workflow automation. Passionate about leveraging low-code orchestration and AI pilot projects to optimize data management workflows, enhance decision-making, and ensure robust data governance and security. Eager to support the CRM Excellence Data Governance team at Siemens Healthineers.</p>
    </div>

    <div class="section">
        <h2 class="section-title">Education</h2>

        <table class="item-header">
            <tr>
                <td>M.Sc. in Autonomy Technologies</td>
                <td class="date">April 2026 – April 2028 (expected)</td>
            </tr>
        </table>
        <div class="item-subheader">Friedrich-Alexander-Universität Erlangen-Nürnberg (FAU), Germany</div>

        <table class="item-header" style="margin-top: 8px;">
            <tr>
                <td>B.Sc. in Computer and Systems Engineering</td>
                <td class="date">Sept 2020 – July 2025</td>
            </tr>
        </table>
        <div class="item-subheader">Mansoura University, Egypt – Graduated with Honors (Excellent Grade)</div>
        <p style="font-size: 9pt; margin: 2px 0;">Relevant Coursework: Machine Learning, Probability & Statistics, Data Structures, Algorithms, Linear Algebra.</p>
    </div>

    <div class="section">
        <h2 class="section-title">Technical Skills</h2>
        <div class="skills-container">
            <div><span class="skill-category">Data Governance & Analysis:</span> Power BI, Excel, Data Cleaning, Secure Data Handling, GDPR Awareness, PostgreSQL.</div>
            <div><span class="skill-category">AI & Workflow Automation:</span> n8n (Low-Code Orchestration analogous to Power Automate), LangChain, RAG, GenAI Agent Design.</div>
            <div><span class="skill-category">Programming & Data Processing:</span> Python (OOP), Pandas, NumPy, FastAPI.</div>
            <div><span class="skill-category">Machine Learning:</span> PyTorch, TensorFlow, Scikit-learn, Classification, Clustering, Data Modeling.</div>
            <div><span class="skill-category">Cloud & Operations:</span> AWS EC2, Docker, Git, Linux.</div>
        </div>
    </div>

    <div class="section">
        <h2 class="section-title">Experience</h2>

        <table class="item-header">
            <tr>
                <td>CRM Developer & Data Science Mentor</td>
                <td class="date">2021 – 2025</td>
            </tr>
        </table>
        <div class="item-subheader">Life Makers Charity & CAT Reloaded, Egypt</div>
        <ul>
            <li>Developed and maintained a custom CRM system managing sensitive data for 500+ volunteers, establishing secure user access and efficient data retrieval workflows.</li>
            <li>Designed and documented onboarding workflows that improved data efficiency and compliance, reducing data retrieval and processing time by 45%.</li>
            <li>Collaborated with administrative users to understand their data usage needs, mentoring a cohort of 20+ peers in Data Science fundamentals and best practices for data handling.</li>
        </ul>

        <table class="item-header">
            <tr>
                <td>Freelance AI & Low-Code Developer</td>
                <td class="date">2024 – Present</td>
            </tr>
        </table>
        <div class="item-subheader">Self-Employed, Remote</div>
        <ul>
            <li>Engineered and managed n8n automation workflows (analogous to Microsoft Power Automate), optimizing data management and reducing manual client data entry workflows by up to 60%.</li>
            <li>Architected pilot AI applications and proof-of-concept projects, focusing on secure data extraction and automated reporting for SME clients.</li>
            <li>Analyzed business datasets exceeding 50,000+ rows using Python, Excel, and Power BI, delivering actionable insights that enhanced strategic decision-making.</li>
        </ul>
    </div>

    <div class="section">
        <h2 class="section-title">Projects</h2>

        <div style="font-weight: bold; font-size: 11pt; margin-top: 5px;">Project Sanad – GenAI Chatbot for Compliance & Case Analysis</div>
        <ul>
            <li>Engineered an end-to-end GenAI pilot project enabling teams to query operational data via chat, ensuring strict alignment with organizational support guidelines and governance rules.</li>
            <li>Improved organizational decision-making and automated compliance evaluations, reducing manual guideline assessment time by 50%. (Stack: LangChain, GPT-4o, FastAPI, ChromaDB).</li>
        </ul>

        <div style="font-weight: bold; font-size: 11pt; margin-top: 5px;">AI Customer Service Agent – Workflow Automation Platform</div>
        <ul>
            <li>Deployed a production-ready AI agent using n8n for task automation, achieving an 85% automated resolution rate while maintaining secure data logs and access management via PostgreSQL. (Stack: n8n, PostgreSQL, AWS EC2).</li>
        </ul>
    </div>

    <div class="section">
        <h2 class="section-title">Certifications & Languages</h2>
        <div style="font-size: 10pt;">
            <strong>Certifications:</strong> DEPI Microsoft ML Engineering, ML & DL Specializations (Coursera), NTI AI Certificate.
        </div>
        <div style="font-size: 10pt; margin-top: 4px;">
            <strong>Languages:</strong> English (Advanced - C1, IELTS Band 7), German (A2), Arabic (Native).
        </div>
    </div>

</body>
</html>
"""

# Ensure weasyprint is installed: pip install weasyprint
output_filename = "Mohamed_Azzam_CRM_Data_Governance_Resume.pdf"
HTML(string=html_content).write_pdf(output_filename)
print(f"Done! Saved as: {output_filename}")