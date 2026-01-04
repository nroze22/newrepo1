# 📖 Usage Guide - AI Consultant Platform

## Quick Start (5 Minutes)

### 1. Get Your Gemini API Key
1. Visit https://makersuite.google.com/app/apikey
2. Sign in with your Google account
3. Click "Create API Key"
4. Copy your key

### 2. Configure the Application
```bash
# Copy the environment file
cp .env.example .env

# Edit .env and add your API key
nano .env

# Add your key:
GEMINI_API_KEY=your_actual_api_key_here
```

### 3. Run the Application
```bash
# Option 1: Using the start script (recommended)
python3 start.py

# Option 2: Manual start
pip install -r requirements.txt
python3 backend/main.py
```

### 4. Open in Browser
Navigate to: http://localhost:8000

---

## 🎬 Using the Platform

### Step 1: Select Your Consultant Mode

Choose the expertise area that matches your needs:

#### 🏢 **Business Strategy** ($199/session)
**Best for:**
- Pitch deck reviews
- Business model validation
- Financial analysis
- Market strategy
- Competitive analysis

**What to show:**
- Presentation slides
- Financial spreadsheets
- Business plans
- Market research data

**Example use case:**
*"I'm preparing to pitch to investors. Let me share my deck and get feedback on my story, market positioning, and financial projections."*

---

#### 🎨 **Design Consultant** ($149/session)
**Best for:**
- UI/UX review
- Brand identity critique
- Visual design analysis
- Accessibility audit
- Design system review

**What to show:**
- Design mockups
- Website/app interfaces
- Branding materials
- Prototypes
- Style guides

**Example use case:**
*"I've designed a new mobile app. I need feedback on the user flow, visual hierarchy, and overall UX."*

---

#### 💻 **Code Architect** ($179/session)
**Best for:**
- Code review
- Architecture analysis
- Performance optimization
- Security audit
- Best practices

**What to show:**
- Code on screen (share screen)
- Architecture diagrams
- Database schemas
- API designs

**Example use case:**
*"I'm building a new feature. Let me walk through my code and get feedback on architecture, potential bugs, and optimization opportunities."*

---

#### ⚖️ **Legal Advisor** ($249/session)
**Best for:**
- Contract review (educational)
- Compliance checking
- Terms of service analysis
- Privacy policy review
- Risk identification

**What to show:**
- Contracts
- Legal documents
- Terms & conditions
- Compliance materials

**⚠️ Important:** This is for educational purposes only. Not legal advice. Consult a licensed attorney for legal decisions.

---

#### 🏥 **Medical Imaging** ($99/session)
**Best for:**
- Educational image analysis
- Pattern recognition
- Learning about conditions
- Research purposes

**What to show:**
- Medical images
- X-rays, MRIs, CT scans (anonymized)
- Educational materials

**⚠️ Important:** Educational purposes only. NOT a medical diagnosis. Always consult healthcare professionals.

---

#### 🏠 **Real Estate Investor** ($129/session)
**Best for:**
- Property analysis
- Investment evaluation
- Market trends
- Renovation opportunities
- ROI calculations

**What to show:**
- Property videos
- Listing photos
- Neighborhood tours
- Floor plans
- Comparable sales data

**Example use case:**
*"I'm considering this investment property. Let me show you a virtual tour and get analysis on value, potential issues, and ROI."*

---

#### 📱 **Marketing Strategist** ($179/session)
**Best for:**
- Campaign review
- Ad creative analysis
- Content strategy
- Brand positioning
- Conversion optimization

**What to show:**
- Marketing materials
- Ad campaigns
- Landing pages
- Email designs
- Social media content

**Example use case:**
*"I'm launching a new campaign. Let me share the creatives and get feedback on messaging, targeting, and expected performance."*

---

### Step 2: Prepare Your Session

**Before starting:**
1. ✅ Test your camera and microphone
2. ✅ Prepare materials to share (slides, documents, designs)
3. ✅ Have specific questions ready
4. ✅ Add custom instructions if needed

**Custom Instructions Examples:**
- "Focus on identifying security vulnerabilities"
- "Prioritize ROI and financial metrics"
- "I'm presenting to technical audiences"
- "Looking specifically at color accessibility"
- "Need help with mobile-first design"

---

### Step 3: Start Your Session

1. **Click "Start Analysis Session"**
   - Browser will request camera/microphone access
   - Allow access to begin

2. **Begin Presenting**
   - Talk through your materials
   - Share your screen if needed
   - The AI analyzes in real-time

3. **Review Insights**
   - Insights appear on the right panel
   - Check different tabs: Insights, Recommendations, Risks, Opportunities
   - Action items are listed at the bottom

---

### Step 4: Interactive Features

#### 💬 Ask Questions
Use the "Ask the AI Consultant" feature for specific questions:
- "What's the biggest risk you see?"
- "How can I improve the ROI?"
- "Is this design accessible?"
- "What would you change first?"

#### 🖥️ Screen Sharing
Click the screen share button to:
- Walk through code
- Demonstrate workflows
- Show live applications
- Review documents

#### 📊 Real-time Analysis
The AI analyzes:
- **Visual content:** What you're showing on camera
- **Audio:** What you're saying
- **Screen shares:** Content you're demonstrating
- **Context:** Building on previous insights

---

### Step 5: End Session & Export

1. **Review Summary**
   - Total insights generated
   - Key recommendations
   - Action items prioritized

2. **Export Report**
   - Click "Export Report"
   - Downloadable Markdown file
   - Includes all insights, recommendations, and action items
   - Professional consulting format

3. **End Session**
   - Click "End Session"
   - Confirm to stop

---

## 💡 Pro Tips

### Getting the Best Results:

1. **Be Structured**
   - Start with context and goals
   - Walk through systematically
   - Pause for AI to analyze

2. **Show, Don't Just Tell**
   - Visual content gets deeper analysis
   - Use screen share liberally
   - Show examples and prototypes

3. **Ask Specific Questions**
   - "What's the conversion rate potential?"
   - "Where are the security risks?"
   - "How can I improve user engagement?"

4. **Use Custom Instructions**
   - Set context upfront
   - Specify your audience
   - Highlight priority areas

5. **Iterate**
   - Start with overview
   - Dive deep into problem areas
   - Ask follow-up questions

### What Makes a Great Session:

✅ **Good:**
- "Here's my SaaS pitch deck. I'm raising a seed round. Focus on market opportunity and competitive moats."
- *Shows slides, discusses each section, asks for specific feedback*

❌ **Less Effective:**
- "Tell me if my business is good"
- *No visuals, vague questions, no context*

---

## 🎯 Example Sessions

### Example 1: Startup Pitch Review

**Mode:** Business Strategy
**Duration:** 30 minutes
**Materials:** 15-slide pitch deck

**Flow:**
1. "I'm raising $2M seed for a B2B SaaS platform"
2. Share screen with deck
3. Walk through each slide (2 min per slide)
4. Ask: "What are my weakest slides?"
5. Ask: "How can I strengthen the market opportunity?"
6. Review recommendations
7. Export report

**Value:** Insights comparable to $2500 consulting session for $199

---

### Example 2: Code Review

**Mode:** Code Architect
**Duration:** 45 minutes
**Materials:** GitHub repository

**Flow:**
1. "I'm building a real-time chat application"
2. Share screen with code editor
3. Walk through architecture
4. Show key files and functions
5. Ask: "Are there performance bottlenecks?"
6. Ask: "What security issues do you see?"
7. Review action items
8. Export report with specific code recommendations

**Value:** Insights comparable to $3000 consulting session for $179

---

### Example 3: Design Critique

**Mode:** Design Consultant
**Duration:** 25 minutes
**Materials:** Figma prototype

**Flow:**
1. "I'm redesigning our e-commerce checkout"
2. Share screen with Figma
3. Click through user flow
4. Show different states (empty, filled, error)
5. Ask: "How can I reduce friction?"
6. Ask: "Is this accessible?"
7. Review recommendations
8. Export report

**Value:** Insights comparable to $1500 consulting session for $149

---

## 🔧 Troubleshooting

### Camera/Microphone Not Working
- **Check browser permissions:** Settings → Privacy → Camera/Microphone
- **Try different browser:** Chrome, Firefox, Safari
- **Check system permissions:** OS settings
- **Restart browser**

### No Insights Appearing
- **Check API key:** Verify GEMINI_API_KEY in .env
- **Check console:** Open browser DevTools (F12) for errors
- **Wait 30 seconds:** Initial analysis takes time
- **Try screen share:** Provides more visual context

### WebSocket Connection Issues
- **Check firewall:** Allow port 8000
- **Check proxy/VPN:** May block WebSocket
- **Restart server:** Stop and start again
- **Try localhost:** Instead of IP address

### Analysis Too Slow
- **Reduce frame rate:** Edit FRAMES_PER_ANALYSIS in .env (increase number)
- **Better internet:** Need good upload speed for video
- **Close other apps:** Free up system resources

### Quality of Insights
- **Add custom instructions:** Guide the AI's focus
- **Be more specific:** Ask targeted questions
- **Show more context:** Visual materials help
- **Speak clearly:** Better audio = better analysis

---

## 📊 Understanding Your Results

### Insights
- Key observations from your presentation
- Patterns and trends identified
- Notable strengths and weaknesses

### Recommendations
- Specific, actionable suggestions
- Prioritized by impact
- Implementation guidance

### Risks
- Potential issues identified
- Things to watch out for
- Red flags to address

### Opportunities
- Areas for improvement
- Growth potential
- Quick wins

### Action Items
- **High Priority:** Address immediately
- **Medium Priority:** Important but not urgent
- **Low Priority:** Nice to have

---

## 💰 Pricing & Plans

### Pay-Per-Session
- **One-time payment:** No commitment
- **Full session access:** Unlimited duration
- **Report included:** Downloadable
- **Best for:** Occasional users

### Subscription Plans

#### Starter - $199/month
- 5 sessions per month
- All consultant modes
- Session recordings
- Email support
- **Best for:** Freelancers, small businesses

#### Professional - $599/month
- 20 sessions per month
- Priority analysis
- Custom consultant modes
- API access
- Priority support
- **Best for:** Growing companies, agencies

#### Enterprise - $1,499/month
- Unlimited sessions
- White-label option
- Dedicated support
- SLA guarantee
- Custom integrations
- **Best for:** Large teams, enterprises

---

## 🚀 Advanced Features

### API Access (Professional+)
```python
import requests

# Create session
response = requests.post('http://localhost:8000/api/sessions/create', json={
    'user_id': 'user123',
    'consultant_mode': 'business'
})

session_id = response.json()['session_id']

# Analyze frame
with open('slide.jpg', 'rb') as f:
    frame_data = base64.b64encode(f.read()).decode()

requests.post(f'http://localhost:8000/api/sessions/{session_id}/analyze', json={
    'session_id': session_id,
    'frame_data': f'data:image/jpeg;base64,{frame_data}',
    'consultant_mode': 'business'
})
```

### Integrations
- **Zoom/Google Meet:** Coming Q2 2026
- **Slack:** Session notifications
- **Notion:** Auto-save reports
- **GitHub:** Code review integration

---

## 🆘 Support

- **Documentation:** https://docs.aiconsultant.com
- **Email:** support@aiconsultant.com
- **Community:** https://community.aiconsultant.com
- **Twitter:** @AIConsultantHQ

---

## 🎓 Learning Resources

- **Video Tutorials:** https://youtube.com/@aiconsultant
- **Blog:** https://blog.aiconsultant.com
- **Webinars:** Monthly expert sessions
- **Case Studies:** Real customer success stories

---

**Ready to replace expensive consultants with AI? Let's go! 🚀**
