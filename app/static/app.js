"use strict";
const $ = (selector) => document.querySelector(selector);
const API = "/api";
const LANGUAGES = {en:"English",es:"Español",fr:"Français",hi:"हिन्दी",hinglish:"Hinglish",mr:"मराठी",unknown:"Not sure yet"};
let csrfToken = "", currentUser = null, currentTopicId = null, activeLessonId = null, accountMode = "login", allResources = [], activeConversationId = null, viewingSavedConversation = false, lastAssistantText = "", lastAssistantLanguage = "en", recognition = null, standaloneMessages = [], cachedVoices = [];
let voicePracticeRunning = false, voiceReplyPending = false, pendingVoiceTranscript = "", voiceSpeechToken = 0, activeQuestionId = null, seenExerciseIds = new Set(), awaitingNextQuestion = false;
let voiceMicWindowActive = false, voiceMetricsStream = null, voiceMetricsContext = null, voiceMetricsAnalyser = null, voiceMetricsTimer = null, voiceMetricsBuffer = null, voiceMetricsSessionToken = 0;
let voiceDeliveryState = createVoiceDeliveryState();
function createVoiceDeliveryState(){return {captureAvailable:false,listeningFrames:0,speechFrames:0,quietFrames:0,wordCount:0,responseCount:0,rmsValues:[],pitchValues:[]}}

function getVoices(){
  if("speechSynthesis" in window){
    const list = window.speechSynthesis.getVoices();
    if(list && list.length) cachedVoices = list;
  }
  return cachedVoices;
}
if("speechSynthesis" in window){
  getVoices();
  window.speechSynthesis.onvoiceschanged = () => { getVoices(); };
}
const VOICE_SPEED_STORAGE_KEY="cognito.voiceSpeed";
const VOICE_SPEEDS=["0.5","1","1.25","1.5"];
function restoreVoiceSpeed(){
  try{
    const saved=localStorage.getItem(VOICE_SPEED_STORAGE_KEY);
    const legacy={"0.82":"1","0.95":"0.5","1.08":"1.25","1.3":"1.5"};
    const normalized=legacy[saved]||saved;
    if(VOICE_SPEEDS.includes(normalized)){$("#voice-speed").value=normalized;if(normalized!==saved)localStorage.setItem(VOICE_SPEED_STORAGE_KEY,normalized)}
  }catch{}
}
restoreVoiceSpeed();
$("#voice-speed").addEventListener("change",()=>{
  const rate=$("#voice-speed").value;
  try{localStorage.setItem(VOICE_SPEED_STORAGE_KEY,rate)}catch{}
  const label=rate==="0.5"?"Slow":rate==="1"?"Normal":rate==="1.5"?"Fast":"Medium";
  if("speechSynthesis" in window&&window.speechSynthesis.speaking&&lastAssistantText)speakPracticeText(lastAssistantText,voicePracticeRunning);
  showToast(`Voice speed set to ${label}. It applies to all assistant voice playback.`);
});

function safeText(value){return String(value ?? "").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"}[c]))}
function showToast(message){const toast=$("#toast");toast.textContent=message;toast.classList.add("show");window.setTimeout(()=>toast.classList.remove("show"),2800)}

function formatBotMessage(text){
  let s = safeText(text);
  // Code blocks ```code```
  s = s.replace(/```(?:[a-zA-Z0-9_-]+)?\n?([\s\S]*?)```/g, '<pre class="msg-code-block"><code>$1</code></pre>');
  // Headings #, ##, ###, ####
  s = s.replace(/^#{1,4}\s*(.+)$/gm, '<strong class="msg-heading">$1</strong>');
  // Lists with bullets (*, -, +, •)
  s = s.replace(/^\s*[-*+•]\s+(.+)$/gm, '<span class="msg-list-item"><span class="msg-bullet">•</span><span>$1</span></span>');
  // Numbered lists (1., 2., 1))
  s = s.replace(/^\s*(\d+)[\.\)]\s+(.+)$/gm, '<span class="msg-list-item"><span class="msg-num">$1.</span><span>$2</span></span>');
  // Bold **text** or __text__
  s = s.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>');
  s = s.replace(/__(.+?)__/g, '<strong>$1</strong>');
  // Italics *text* or _text_
  s = s.replace(/\*([^*\n]+?)\*/g, '<em>$1</em>');
  s = s.replace(/_([^_\n]+?)_/g, '<em>$1</em>');
  // Inline code `code`
  s = s.replace(/`([^`\n]+)`/g, '<code class="msg-code">$1</code>');
  // Paragraph line breaks
  s = s.replace(/\n{2,}/g, '<br><br>');
  s = s.replace(/\n/g, '<br>');
  return s;
}

function stripMarkdown(text){
  return text
    .replace(/```[\s\S]*?```/g, ' ')
    .replace(/#{1,4}\s*/g, '')
    .replace(/\*{1,2}([^*]+)\*{1,2}/g, '$1')
    .replace(/_{1,2}([^_]+)_{1,2}/g, '$1')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/^\s*[-*+•]\s+/gm, '')
    .replace(/\s+/g, ' ')
    .trim();
}
async function api(path,options={}){
  const method=(options.method||"GET").toUpperCase();const headers=new Headers(options.headers||{});
  if(options.body&&!headers.has("Content-Type"))headers.set("Content-Type","application/json");
  if(method!=="GET"&&method!=="HEAD"&&csrfToken)headers.set("X-CSRF-Token",csrfToken);
  let response;try{response=await fetch(`${API}${path}`,{...options,method,headers,credentials:"same-origin"})}catch{throw new Error("The service could not be reached. Start the local server and refresh the page.")}
  const data=await response.json().catch(()=>({}));if(!response.ok){if(response.status===403&&typeof data.detail==="string"&&data.detail.includes("Session expired")){try{const refreshed=await fetch(`${API}/session`,{credentials:"same-origin"}).then(r=>r.json());if(refreshed.csrf_token)csrfToken=refreshed.csrf_token}catch{}}const message=typeof data.detail==="string"?data.detail:Array.isArray(data.detail)?data.detail.map(item=>[item.field,item.message].filter(Boolean).join(": ")).join(" "):"Something went wrong. Please try again.";throw new Error(message)}return data;
}
function addMessage(role,text){const item=document.createElement("div");item.className=`message ${role}`;if(role==="bot"){const avatar=document.createElement("div");avatar.className="bot-avatar";avatar.textContent="◎";item.append(avatar);lastAssistantText=text}const wrap=document.createElement("div"),bubble=document.createElement("div"),time=document.createElement("div");bubble.className="bubble";if(role==="bot"){bubble.innerHTML=formatBotMessage(text)}else{bubble.textContent=text}time.className="message-time";time.textContent=new Date().toLocaleTimeString([],{hour:"2-digit",minute:"2-digit"});wrap.append(bubble,time);item.append(wrap);$("#messages").append(item);$("#messages").scrollTop=$("#messages").scrollHeight}
function showTranscript(messages){$("#messages").replaceChildren();lastAssistantText="";for(const item of messages||[])addMessage(item.role==="assistant"?"bot":"user",item.content)}
const MODE_LABELS={casual:"Casual conversation",english:"English practice",interview:"Interview practice",interview_hr:"HR interview practice",interview_tech:"Technical interview practice",mock_interview:"Mock interview",roleplay:"Role-play"};
const MOCK_ROLE_BANK={
  computer:{
    school:["Computer Operator Trainee","IT Support Trainee","Data Entry Operator","Web Development Trainee"],
    diploma:["Junior Software Developer","Web Developer Intern","Software Testing Intern","IT Support Engineer","Network Support Trainee","Cybersecurity Intern","Data Analyst Intern"],
    undergraduate:["Software Developer Intern","Frontend Developer Intern","Backend Developer Intern","QA / Software Testing Intern","Data Analyst Intern","Cybersecurity Intern","Cloud Support Intern"],
    graduate:["Associate Software Engineer","Junior Software Developer","QA Engineer","Data Analyst","IT Support Engineer","Cybersecurity Analyst","Frontend Developer"],
    postgraduate:["Software Engineer","AI / ML Engineer","Data Scientist","Cybersecurity Analyst","Cloud Engineer","Research Assistant (Computing)"],
    other:["IT Support Trainee","Web Development Trainee","Software Testing Intern","Computer Operator","Data Entry Operator"]
  },
  engineering:{
    school:["Technical Support Trainee","Junior Technician Trainee","Quality Inspector Trainee"],
    diploma:["Junior Technician","CAD Technician Trainee","Quality Assurance Trainee","Production Technician","Electrical / Mechanical Support Technician"],
    undergraduate:["Graduate Engineer Trainee","Quality Engineer Intern","Project Engineer Intern","Embedded Systems Intern","Design Engineer Intern"],
    graduate:["Graduate Engineer Trainee","Junior Engineer","Quality Engineer","Project Engineer","Systems Engineer"],
    postgraduate:["R&D Engineer","Project Engineer","Systems Engineer","Engineering Analyst"],
    other:["Technical Support Trainee","Junior Technician Trainee","Quality Inspector Trainee"]
  },
  business:{
    school:["Sales Trainee","Customer Support Trainee","Office Assistant"],
    diploma:["Sales Executive Trainee","Operations Assistant","Customer Success Trainee","Marketing Intern"],
    undergraduate:["Business Development Intern","HR Intern","Operations Intern","Marketing Intern","Business Analyst Intern"],
    graduate:["Business Analyst","HR Associate","Operations Executive","Marketing Executive","Customer Success Associate"],
    postgraduate:["Management Trainee","Business Analyst","Project Coordinator","Operations Analyst","HR Executive"],
    other:["Sales Trainee","Customer Support Executive","Office Assistant","Operations Assistant"]
  },
  commerce:{
    school:["Accounts Assistant Trainee","Data Entry Operator","Sales Trainee"],
    diploma:["Accounts Assistant","Billing Executive","Finance Intern","Office Administrator Trainee"],
    undergraduate:["Accounting Intern","Finance Intern","Tax Assistant","Business Analyst Intern","Banking Operations Intern"],
    graduate:["Junior Accountant","Accounts Executive","Financial Analyst Trainee","Banking Operations Associate","Audit Associate"],
    postgraduate:["Financial Analyst","Accountant","Audit Associate","Finance Executive","Risk Analyst"],
    other:["Accounts Assistant","Billing Executive","Data Entry Operator","Office Administrator"]
  },
  arts:{
    school:["Content Writing Trainee","Customer Support Trainee","Retail Associate"],
    diploma:["Content Writing Intern","Social Media Assistant","Customer Support Executive","Digital Marketing Trainee"],
    undergraduate:["Content Writer Intern","Digital Marketing Intern","Social Media Intern","HR Intern","Public Relations Intern"],
    graduate:["Content Writer","Digital Marketing Executive","Social Media Executive","HR Associate","Communications Associate"],
    postgraduate:["Communications Specialist","Content Strategist","Digital Marketing Manager Trainee","HR Executive","Public Relations Associate"],
    other:["Content Writing Trainee","Customer Support Executive","Digital Marketing Trainee","Office Assistant"]
  },
  science:{
    school:["Lab Assistant Trainee","Data Entry Operator","Science Tutor Assistant"],
    diploma:["Lab Technician Trainee","Quality Testing Assistant","Research Assistant Trainee"],
    undergraduate:["Research Intern","Data Analyst Intern","Quality Assurance Intern","Laboratory Intern"],
    graduate:["Research Assistant","Data Analyst","Quality Analyst","Laboratory Technician","Junior QA Analyst"],
    postgraduate:["Research Associate","Data Scientist Trainee","Laboratory Analyst","Research Analyst","Quality Analyst"],
    other:["Lab Assistant Trainee","Quality Testing Assistant","Data Entry Operator"]
  },
  healthcare:{
    school:["Patient Support Trainee","Clinic Assistant Trainee","Healthcare Data Entry Operator"],
    diploma:["Medical Lab Technician Trainee","Patient Care Assistant","Clinic Administrator Trainee"],
    undergraduate:["Healthcare Operations Intern","Clinical Research Intern","Health Data Analyst Intern","Patient Support Intern"],
    graduate:["Healthcare Administrator","Clinical Research Coordinator","Medical Lab Technician","Healthcare Data Analyst","Patient Support Associate"],
    postgraduate:["Clinical Research Associate","Healthcare Analyst","Public Health Research Assistant","Clinical Data Coordinator"],
    other:["Patient Support Trainee","Clinic Assistant Trainee","Healthcare Data Entry Operator"]
  },
  general:{
    school:["Customer Support Trainee","Office Assistant","Sales Trainee","Data Entry Operator"],
    diploma:["Customer Support Executive","Office Administrator Trainee","Operations Assistant","Sales Associate"],
    undergraduate:["Customer Success Intern","Operations Intern","Project Coordinator Intern","Sales and Marketing Intern"],
    graduate:["Customer Success Associate","Operations Executive","Project Coordinator","Sales Executive","Administrative Associate"],
    postgraduate:["Operations Analyst","Project Coordinator","Customer Success Manager Trainee","Program Associate"],
    other:["Customer Support Executive","Office Assistant","Sales Associate","Operations Assistant"]
  }
};
const EDUCATION_TIER={secondary:"school",higher_secondary:"school",diploma:"diploma",undergraduate:"undergraduate",graduate:"graduate",postgraduate:"postgraduate",other:"other"};
function updateMockRoles(selectedRole=""){
  const level=$("#education-level").value,field=$("#education-field").value,roleSelect=$("#mock-interview-role");
  roleSelect.replaceChildren();
  if($("#conversation-mode").value!=="mock_interview"){roleSelect.disabled=true;roleSelect.add(new Option("Choose Mock interview mode first",""));return}
  if(!level){roleSelect.disabled=true;roleSelect.add(new Option("Select education first",""));return}
  if(!field){roleSelect.disabled=true;roleSelect.add(new Option("Select your field first",""));return}
  const tier=EDUCATION_TIER[level]||"other";
  const roles=MOCK_ROLE_BANK[field]?.[tier]||MOCK_ROLE_BANK.general[tier];
  roleSelect.add(new Option("Select a matching role…",""));
  roles.forEach(role=>roleSelect.add(new Option(role,role)));
  roleSelect.disabled=false;
  if(selectedRole&&roles.includes(selectedRole))roleSelect.value=selectedRole;
}
function updateMockInterviewControls(){
  const isMock=$("#conversation-mode").value==="mock_interview";
  $("#mock-interview-config").classList.toggle("hidden",!isMock);
  $("#practice-context-field").classList.toggle("hidden",isMock);
  $("#education-field").disabled=!$("#education-level").value||isMock===false;
  if(!isMock){$("#education-field").disabled=true;$("#mock-interview-role").disabled=true;}
  else updateMockRoles($("#mock-interview-role").value);
}
function setSessionStatus(message,error=false){const status=$("#session-status");status.textContent=message;status.classList.toggle("error",error)}
function updateContextLabel(){const mode=$("#conversation-mode").value;const isInterview=["interview","interview_hr","interview_tech","mock_interview"].includes(mode);$("#context-label").textContent=isInterview?"TARGET ROLE":mode==="roleplay"?"SCENARIO":"TOPIC OR ROLE";$("#practice-context").placeholder=mode==="interview_tech"?"e.g. backend engineer, frontend dev":mode==="interview_hr"?"e.g. culture fit, leadership, teamwork":isInterview?"e.g. software internship":"e.g. travel, hobbies"}
function setStatus(text,ok=true){$("#api-status").innerHTML=`<i style="background:${ok?"#56ae82":"#d47c63"}"></i> ${safeText(text)}`}
function setAccount(user){currentUser=user;const button=$("#account-button"),name=$("#sidebar-account-name"),note=$("#sidebar-account-note");if(user){button.textContent=user.display_name.slice(0,1).toUpperCase();button.title=`Signed in as ${user.display_name}`;name.textContent=user.display_name;note.textContent="Your progress is syncing";$("#progress-caption").textContent="Your learning history";if(user.preferred_language)$("#chat-language").value=user.preferred_language}else{button.textContent="S";button.title="Sign in or create an account";name.textContent="Guest session";note.textContent="Sign in to save across devices";$("#progress-caption").textContent="Sign in to keep your learning history"}}
async function loadSession(){const data=await api("/session");csrfToken=data.csrf_token||"";setAccount(data.user);return data}
function renderContext(data){$("#insight-empty").classList.add("hidden");$("#insight-content").classList.remove("hidden");$("#language-value").textContent=LANGUAGES[data.language]||"Not sure yet";const first=data.resources?.[0];currentTopicId=first?.id||null;$("#topic-value").textContent=first?.title||"Keep exploring";$("#topic-open").disabled=!first;$("#context-guides").innerHTML=(data.resources||[]).slice(1,4).map(item=>`<button class="guide-chip" type="button" data-open-resource="${safeText(item.id)}">${safeText(item.title)}</button>`).join("")||'<span class="empty-inline">No related guide yet</span>';$("#tutor-note").textContent=data.tutor_available?"Messages and related guide text are sent to the configured tutor provider. Cognito does not save chat messages.":"Open-ended tutoring needs an AI provider. You can still search the library and practice lessons."}
async function sendMessage(text){text=text.trim();if(!text)return false;if(viewingSavedConversation){setSessionStatus("This saved session is read-only. Start a new practice to continue.",true);return false}$(".suggestion-row")?.remove();addMessage("user",text);const input=$("#chat-input"),submit=$("#chat-form button[type=submit]");input.value="";input.style.height="auto";submit.disabled=true;const pending=document.createElement("div");pending.className="message bot typing-message";pending.innerHTML='<div class="bot-avatar">◎</div><div class="bubble typing-dots" aria-label="Thinking"><i></i><i></i><i></i></div>';$("#messages").append(pending);$("#messages").scrollTop=$("#messages").scrollHeight;
  try{const data=activeConversationId?await api(`/conversations/${encodeURIComponent(activeConversationId)}/messages`,{method:"POST",body:JSON.stringify({message:text})}):await api("/chat",{method:"POST",body:JSON.stringify({message:text,language:$("#chat-language").value,history:standaloneMessages.slice(-16)})});pending.remove();if(activeConversationId){showTranscript(data.messages);if(data.offline_fallback){setSessionStatus("Offline practice prompts are active because the AI provider is unavailable.");$("#provider-disclosure").textContent="Offline practice mode is active. The session report uses basic transcript heuristics and any available local voice-delivery summaries; raw microphone audio is not uploaded or stored.";$("#provider-disclosure").classList.remove("hidden")}else{setSessionStatus(`${MODE_LABELS[$("#conversation-mode").value]} · saved to your account.`)}}else{standaloneMessages=[...standaloneMessages,{role:"user",content:text},{role:"assistant",content:data.reply}].slice(-40);lastAssistantLanguage=data.language||(detectTextLang(data.reply)?"hi":"en");addMessage("bot",data.reply);renderContext(data)}return true}catch(error){pending.remove();input.value=text;input.style.height="auto";input.style.height=`${Math.min(input.scrollHeight,100)}px`;addMessage("notice",`${error.message} Your message is back in the box so you can retry.`);showToast(error.message);if(activeConversationId)setSessionStatus(error.message,true);return false}finally{submit.disabled=false;if(!voicePracticeRunning)input.focus()}}
$("#chat-form").addEventListener("submit",async event=>{event.preventDefault();const isVoiceTurn=voicePracticeRunning&&Boolean(activeConversationId);if(isVoiceTurn){voiceReplyPending=true;recognition?.stop();$("#end-session").disabled=true}const ok=await sendMessage($("#chat-input").value);if(isVoiceTurn){voiceReplyPending=false;$("#end-session").disabled=!activeConversationId;if(voicePracticeRunning&&ok)speakPracticeText(lastAssistantText,true);else if(voicePracticeRunning&&!ok){voicePracticeRunning=false;setSessionStatus("Voice turn failed. Retry using the message box, then stop to generate a report.",true)}}});
$("#chat-input").addEventListener("input",event=>{event.target.style.height="auto";event.target.style.height=`${Math.min(event.target.scrollHeight,100)}px`});
$("#chat-input").addEventListener("keydown",event=>{if(event.key==="Enter"&&!event.shiftKey){event.preventDefault();$("#chat-form").requestSubmit()}});
$("#conversation-mode").addEventListener("change",()=>{updateContextLabel();updateMockInterviewControls()});
$("#education-level").addEventListener("change",()=>{$("#education-field").value="";$("#mock-interview-role").value="";$("#education-field").disabled=!$("#education-level").value;updateMockRoles()});
$("#education-field").addEventListener("change",()=>updateMockRoles());
updateContextLabel();updateMockInterviewControls();
async function loadConversationHistory(){const root=$("#conversation-list");if(!currentUser){root.innerHTML='<span class="empty-inline">Sign in to save conversation history.</span>';return}root.innerHTML='<span class="empty-inline">Loading practice history…</span>';try{const data=await api("/conversations");if(!data.conversations.length){root.innerHTML='<span class="empty-inline">Your completed practices will appear here.</span>';return}root.innerHTML=data.conversations.map(item=>{const hasReport=item.report_status==="ready";return`<div class="history-row"><button type="button" class="history-item" data-conversation="${safeText(item.id)}"><strong>${safeText(MODE_LABELS[item.mode]||"Practice")} · ${safeText(item.settings?.role||item.settings?.scenario||item.settings?.level||"")}</strong><span>${item.status==="completed"?"REVIEW":"IN PROGRESS"}</span></button>${hasReport?`<button type="button" class="text-link report-link" data-view-report="${safeText(item.id)}" title="View performance report">Report ↗</button>`:""}</div>`}).join("")}catch(error){root.innerHTML=`<span class="empty-inline">${safeText(error.message)}</span>`}}
function setPracticeActive(active){$("#start-session").disabled=active;$("#end-session").disabled=!active;$("#cancel-session").disabled=!active;$("#conversation-mode").disabled=active;$("#practice-level").disabled=active;$("#practice-context").disabled=active;$("#correction-preference").disabled=active;$("#chat-language").disabled=active;$("#voice-practice-mode").disabled=active;$("#education-level").disabled=active;$("#education-field").disabled=active||$("#conversation-mode").value!=="mock_interview"||!$("#education-level").value;$("#mock-interview-role").disabled=active||$("#conversation-mode").value!=="mock_interview"||!$("#education-level").value||!$("#education-field").value}
$("#start-session").addEventListener("click",async()=>{
  if(!currentUser){setSessionStatus("Sign in to start a saved conversation practice.",true);openAccount();return}
  if(activeConversationId){setSessionStatus("A practice session is already active. End or cancel it before starting another.",true);return;}
  const mode=$("#conversation-mode").value,language=$("#chat-language").value,isMockInterview=mode==="mock_interview";
  if(isMockInterview&&(!$("#education-level").value||!$("#education-field").value||!$("#mock-interview-role").value)){
    setSessionStatus("First select your education, then field of study, and finally a matching role.",true);
    showToast("Complete the mock interview education and role selections first.");
    return;
  }
  const isLegacyInterview=["interview","interview_hr","interview_tech"].includes(mode);
  const body={
    mode,level:$("#practice-level").value,
    language:language==="auto"?(currentUser.preferred_language&&currentUser.preferred_language!=="auto"?currentUser.preferred_language:"en"):language,
    role:isMockInterview?$("#mock-interview-role").value:(isLegacyInterview?$("#practice-context").value:""),
    scenario:mode==="roleplay"?$("#practice-context").value:"",
    education_level:isMockInterview?$("#education-level").value:"",
    education_field:isMockInterview?$("#education-field").value:"",
    corrections:$("#correction-preference").checked
  };
  const button=$("#start-session");button.disabled=true;setSessionStatus("Starting your practice…");
  try{
    const session=await api("/conversations",{method:"POST",body:JSON.stringify(body)});
    activeConversationId=session.id;lastAssistantLanguage=session.settings?.language||"en";standaloneMessages=[];viewingSavedConversation=false;voiceDeliveryState=createVoiceDeliveryState();
    showTranscript(session.messages);setPracticeActive(true);
    if(session.settings?.offline_fallback){setSessionStatus("Offline practice prompts are active. Your report will be a basic transcript estimate.");$("#provider-disclosure").textContent=`Offline practice mode is active because the AI provider is unavailable${session.settings.offline_reason?`: ${session.settings.offline_reason}`:""}. Your transcript is saved to your account. The report is a basic text-based estimate; pronunciation and voice delivery are not assessed.`}
    else{setSessionStatus(`${MODE_LABELS[mode]} started · voice and transcript practice ready.`);$("#provider-disclosure").textContent="Conversation text is sent to the configured AI provider and saved to your account. Cognito does not upload or store raw audio recordings; browser speech recognition may process audio through its own service. Aggregate pace, pause, pitch-variation and volume-variation metrics may be sent with the report."}
    $("#provider-disclosure").classList.remove("hidden");await loadConversationHistory();if($("#voice-practice-mode").checked)startVoicePractice();
  }catch(error){setSessionStatus(error.message,true);button.disabled=false;showToast(error.message)}
});
async function endPractice(){if(!activeConversationId)return;if(voicePracticeRunning||recognition)stopVoicePractice();const id=activeConversationId;$("#end-session").disabled=true;setSessionStatus("Finishing practice and generating your performance report…");try{const voice_metrics=await finishVoiceMetricsCapture();const result=await api(`/conversations/${encodeURIComponent(id)}/end`,{method:"POST",body:JSON.stringify({voice_metrics})});activeConversationId=null;viewingSavedConversation=false;voiceReplyPending=false;pendingVoiceTranscript="";voiceSpeechToken++;voiceDeliveryState=createVoiceDeliveryState();setPracticeActive(false);if(result.feedback){addMessage("bot",`Session feedback · transcript-based\n${result.feedback}`)}if(result.report&&result.report_status==="ready"){renderReport(result.report);setSessionStatus(result.report.rubric_version==="offline-1.1"?"Practice ended. Your offline transcript and voice estimate are ready.":"Practice ended. Your performance report is ready.")}else if(result.feedback){setSessionStatus("Practice ended. Your text-based feedback is ready.")}else{setSessionStatus("Practice ended. You can review the transcript in your history.")}await loadConversationHistory()}catch(error){$("#end-session").disabled=false;setSessionStatus(error.message,true);showToast(error.message)}}
$("#end-session").addEventListener("click",endPractice);
async function cancelPractice(){
  if(!activeConversationId)return;
  const id=activeConversationId;
  if(!window.confirm("Cancel this conversation? No performance report will be generated."))return;
  $("#cancel-session").disabled=true; $("#end-session").disabled=true;
  setSessionStatus("Cancelling conversation…");
  stopVoicePractice();
  await finishVoiceMetricsCapture();
  try{
    await api(`/conversations/${encodeURIComponent(id)}/cancel`,{method:"POST"});
    activeConversationId=null; viewingSavedConversation=false; voiceDeliveryState=createVoiceDeliveryState();
    setPracticeActive(false);
    showTranscript([{role:"assistant",content:"This conversation was cancelled. No report was generated. Choose your settings and start a new practice whenever you're ready."}]);
    setSessionStatus("Conversation cancelled. You can start a fresh practice now.");
    await loadConversationHistory();
  }catch(error){
    setSessionStatus(error.message,true); $("#cancel-session").disabled=false;
  }
}
$("#cancel-session").addEventListener("click",cancelPractice);
$("#conversation-list").addEventListener("click",async event=>{const reportBtn=event.target.closest("[data-view-report]");if(reportBtn){try{const data=await api(`/conversations/${encodeURIComponent(reportBtn.dataset.viewReport)}/report`);if(data.report)renderReport(data.report)}catch(error){showToast(error.message)}return}const button=event.target.closest("[data-conversation]");if(!button)return;try{const session=await api(`/conversations/${encodeURIComponent(button.dataset.conversation)}`);activeConversationId=session.status==="active"?session.id:null;viewingSavedConversation=session.status==="completed";$("#conversation-mode").value=session.mode;$("#practice-level").value=session.settings.level;$("#chat-language").value=session.settings.language;$("#practice-context").value=session.settings.role||session.settings.scenario||"";$("#correction-preference").checked=session.settings.corrections;$("#education-level").value=session.settings.education_level||"";$("#education-field").value=session.settings.education_field||"";$("#education-field").disabled=!$("#education-level").value||session.mode!=="mock_interview";updateContextLabel();updateMockInterviewControls();if(session.mode==="mock_interview")updateMockRoles(session.settings.role||"");showTranscript(session.messages);setPracticeActive(session.status==="active");$("#end-session").disabled=session.status!=="active";setSessionStatus(session.status==="active"?(session.settings?.offline_fallback?"Resumed offline practice prompts.":"Resumed your saved practice."):"Reviewing a completed, read-only practice.");if(session.summary)addMessage("bot",`Session feedback · text only\n${session.summary}`)}catch(error){showToast(error.message)}});
$("#clear-history").addEventListener("click",async()=>{if(!currentUser){showToast("Sign in to manage saved practice history.");return}if(activeConversationId){setSessionStatus("End your active practice before clearing saved history.",true);return}if(!window.confirm("Clear all saved conversation practice history? This cannot be undone."))return;try{await api("/conversations",{method:"DELETE"});viewingSavedConversation=false;showTranscript([{role:"assistant",content:"Saved conversation history cleared. Choose a practice mode to start a new practice, or ask a question."}]);setSessionStatus("Saved conversation history cleared.");await loadConversationHistory()}catch(error){showToast(error.message)}});
const SpeechRecognitionAPI=window.SpeechRecognition||window.webkitSpeechRecognition;
$("#voice-start").addEventListener("click",()=>{
  const status=$("#voice-status");
  if(voicePracticeRunning){status.textContent="Voice practice is already listening automatically. Speak when the interviewer finishes.";return;}
  if(!SpeechRecognitionAPI){
    status.textContent="Voice input is not supported in this browser. You can still type your response.";
    status.classList.add("error");
    return;
  }
  if(!window.isSecureContext&&location.hostname!=="localhost"&&location.hostname!=="127.0.0.1"){
    status.textContent="Voice input requires HTTPS or localhost.";
    status.classList.add("error");
    return;
  }
  try{
    recognition=new SpeechRecognitionAPI();
    recognition.lang=({auto:"en-IN",en:"en-IN",es:"es-ES",fr:"fr-FR",hi:"hi-IN",hinglish:"hi-IN",mr:"mr-IN"})[$("#chat-language").value]||"en-IN";
    recognition.interimResults=true;
    recognition.continuous=false;
    recognition.onstart=()=>{
      status.textContent="Listening… Speak now. Your recording is not stored.";
      status.classList.remove("error");
      $("#voice-start").classList.add("hidden");
      $("#voice-stop").classList.remove("hidden");
    };
    recognition.onresult=event=>{
      $("#chat-input").value=Array.from(event.results).map(result=>result[0].transcript).join("");
      $("#chat-input").dispatchEvent(new Event("input"));
    };
    recognition.onerror=event=>{
      status.textContent=event.error==="not-allowed"?"Microphone permission was denied. Allow it in your browser settings or type instead.":`Voice input failed (${event.error}). You can type instead.`;
      status.classList.add("error");
    };
    recognition.onend=()=>{
      $("#voice-start").classList.remove("hidden");
      $("#voice-stop").classList.add("hidden");
      recognition=null;
    };
    recognition.start();
  }catch{
    status.textContent="The microphone could not be started. Check browser permission and try again.";
    status.classList.add("error");
  }
});
$("#voice-stop").addEventListener("click",()=>{if(voicePracticeRunning){stopVoicePractice();setSessionStatus("Voice listening paused. You can type a reply or stop to generate the report.")}else recognition?.stop()});

function detectTextLang(text){
  if(/[\u0900-\u097F]/.test(text)) return "hi";
  return null;
}

function pickVoice(targetLang){
  const voices=getVoices();
  if(!voices||!voices.length)return null;
  const desired=targetLang==="hi"||targetLang==="hinglish"?"hi":(targetLang||"en").split("-")[0].toLowerCase();
  const qualityNames=["natural","neural","google","aria","jenny","ava","samantha","zira","guy","sonia","libby","ryan","oliver","allison","microsoft online"];
  const indianNames=["india","heera","ravi","neerja","prabhat","swara","madhur","hemant","kalpana"];
  const compatible=voices.filter(voice=>{
    const lang=(voice.lang||"").toLowerCase().replace("_","-");
    return lang===desired||lang.startsWith(`${desired}-`);
  });
  if(!compatible.length)return null;
  const scoreVoice=voice=>{
    const lang=(voice.lang||"").toLowerCase().replace("_","-");
    const name=(voice.name||"").toLowerCase();
    let score=lang===desired?38:30;
    if(qualityNames.some(word=>name.includes(word)))score+=32;
    if(name.includes("natural")||name.includes("neural"))score+=15;
    if(desired==="hi"&&indianNames.some(word=>name.includes(word)))score+=25;
    if(desired==="en"&&lang.startsWith("en-in"))score+=9;
    if(desired==="en"&&lang.startsWith("en-gb"))score+=4;
    if(voice.default)score+=2;
    return score;
  };
  return compatible.sort((a,b)=>scoreVoice(b)-scoreVoice(a))[0]||null;
}

function speakPracticeText(text, listenAfter = false){
  const status=$("#voice-status");
  if(!("speechSynthesis" in window)||!("SpeechSynthesisUtterance" in window)){
    status.textContent="Spoken playback is not supported in this browser. You can still use the text conversation.";
    status.classList.add("error");
    if(listenAfter)voicePracticeRunning=false;
    return;
  }
  if(!text){status.textContent="There is no assistant response to play yet.";return;}
  const token=++voiceSpeechToken;
  window.speechSynthesis.cancel();
  const cleanText=stripMarkdown(text);
  const utterance=new SpeechSynthesisUtterance(cleanText);
  const selectedLang=$("#chat-language").value;
  const isHindiText=detectTextLang(cleanText)==="hi"||lastAssistantLanguage==="hi"||selectedLang==="hi";
  const targetLang=isHindiText?"hi":(selectedLang==="auto"?"en":selectedLang);
  const langCode=isHindiText?"hi-IN":({auto:"en-IN",en:"en-IN",es:"es-ES",fr:"fr-FR",hinglish:"hi-IN",mr:"mr-IN",hi:"hi-IN"})[targetLang]||"en-IN";
  utterance.lang=langCode;
  const selectedRate=Number($("#voice-speed").value);
  utterance.rate=VOICE_SPEEDS.includes(String(selectedRate))?selectedRate:1;
  utterance.pitch=1.02;
  utterance.volume=1;
  const voice=pickVoice(targetLang);
  if(voice){utterance.voice=voice;status.textContent=`Speaking with ${voice.name} (${isHindiText ? "Hindi" : "selected language"}).`}
  else status.textContent=`Speaking aloud (${isHindiText ? "Hindi" : "selected language"}).`;
  status.classList.remove("error");
  utterance.onend=()=>{if(token!==voiceSpeechToken)return;$("#speech-pause").textContent="Pause";if(listenAfter&&voicePracticeRunning)startVoicePracticeListening()};
  utterance.onerror=()=>{if(token!==voiceSpeechToken)return;status.textContent="Spoken playback could not complete. You can read the response above.";status.classList.add("error");$("#speech-pause").textContent="Pause";if(listenAfter&&voicePracticeRunning)startVoicePracticeListening()};
  window.speechSynthesis.speak(utterance);
  $("#speech-pause").textContent="Pause";
}

function medianNumber(values){if(!values.length)return null;const sorted=[...values].sort((a,b)=>a-b);const mid=Math.floor(sorted.length/2);return sorted.length%2?sorted[mid]:(sorted[mid-1]+sorted[mid])/2}
function coefficientOfVariation(values){if(values.length<4)return null;const mean=values.reduce((sum,value)=>sum+value,0)/values.length;if(!mean)return null;const variance=values.reduce((sum,value)=>sum+((value-mean)**2),0)/values.length;return Math.sqrt(variance)/mean}
function estimatePitchHz(samples,sampleRate){
  if(!samples||samples.length<1024)return null;
  const reducedLength=Math.floor(samples.length/2),reduced=new Float32Array(reducedLength);let mean=0;
  for(let i=0;i<reducedLength;i++){reduced[i]=samples[i*2];mean+=reduced[i]}
  mean/=reducedLength;let energy=0;
  for(let i=0;i<reducedLength;i++){reduced[i]-=mean;energy+=reduced[i]*reduced[i]}
  if(energy/reducedLength<0.00006)return null;
  const reducedRate=sampleRate/2,minLag=Math.floor(reducedRate/350),maxLag=Math.min(Math.floor(reducedRate/75),Math.floor(reducedLength/2));
  let bestLag=0,bestCorrelation=0;
  for(let lag=minLag;lag<=maxLag;lag++){
    let correlation=0,leftEnergy=0,rightEnergy=0;
    for(let i=0;i<reducedLength-lag;i++){const left=reduced[i],right=reduced[i+lag];correlation+=left*right;leftEnergy+=left*left;rightEnergy+=right*right}
    const normalized=correlation/Math.sqrt((leftEnergy*rightEnergy)||1);
    if(normalized>bestCorrelation){bestCorrelation=normalized;bestLag=lag}
  }
  if(bestCorrelation<0.42||!bestLag)return null;
  const hz=reducedRate/bestLag;
  return hz>=70&&hz<=400?hz:null;
}
async function startVoiceMetricsCapture(){
  if(voiceMetricsStream||voiceMetricsTimer)return;
  voiceDeliveryState=createVoiceDeliveryState();
  if(!navigator.mediaDevices?.getUserMedia||!(window.AudioContext||window.webkitAudioContext))return;
  const sessionToken=++voiceMetricsSessionToken;
  try{
    const stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true,autoGainControl:true}});
    if(sessionToken!==voiceMetricsSessionToken){stream.getTracks().forEach(track=>track.stop());return}
    voiceMetricsStream=stream;
    const AudioContextType=window.AudioContext||window.webkitAudioContext;
    voiceMetricsContext=new AudioContextType();
    const source=voiceMetricsContext.createMediaStreamSource(voiceMetricsStream);
    voiceMetricsAnalyser=voiceMetricsContext.createAnalyser();voiceMetricsAnalyser.fftSize=2048;voiceMetricsAnalyser.smoothingTimeConstant=0;
    source.connect(voiceMetricsAnalyser);voiceMetricsBuffer=new Float32Array(voiceMetricsAnalyser.fftSize);voiceDeliveryState.captureAvailable=true;
    let frame=0;
    voiceMetricsTimer=window.setInterval(()=>{
      if(!voiceMicWindowActive||!voiceMetricsAnalyser||!voiceMetricsBuffer)return;
      voiceMetricsAnalyser.getFloatTimeDomainData(voiceMetricsBuffer);
      let sum=0;for(const sample of voiceMetricsBuffer)sum+=sample*sample;
      const rms=Math.sqrt(sum/voiceMetricsBuffer.length);
      voiceDeliveryState.listeningFrames++;
      if(rms>0.014){
        voiceDeliveryState.speechFrames++;voiceDeliveryState.rmsValues.push(rms);frame++;
        if(frame%3===0){const pitch=estimatePitchHz(voiceMetricsBuffer,voiceMetricsContext.sampleRate);if(pitch)voiceDeliveryState.pitchValues.push(pitch)}
      }else voiceDeliveryState.quietFrames++;
    },100);
    if(voiceMetricsContext.state==="suspended")await voiceMetricsContext.resume().catch(()=>{});
  }catch{
    voiceDeliveryState.captureAvailable=false;
    if(voiceMetricsStream){voiceMetricsStream.getTracks().forEach(track=>track.stop());voiceMetricsStream=null}
    voiceMetricsTimer&&clearInterval(voiceMetricsTimer);voiceMetricsTimer=null;
    if(voiceMetricsContext){voiceMetricsContext.close().catch(()=>{});voiceMetricsContext=null}
  }
}
async function finishVoiceMetricsCapture(){
  voiceMicWindowActive=false;
  voiceMetricsSessionToken++;
  if(voiceMetricsTimer){clearInterval(voiceMetricsTimer);voiceMetricsTimer=null}
  if(voiceMetricsStream){voiceMetricsStream.getTracks().forEach(track=>track.stop());voiceMetricsStream=null}
  if(voiceMetricsContext){try{await voiceMetricsContext.close()}catch{}voiceMetricsContext=null}
  const state=voiceDeliveryState,totalFrames=state.listeningFrames;
  const pitchMedian=medianNumber(state.pitchValues),pitchVariation=coefficientOfVariation(state.pitchValues),volumeVariation=coefficientOfVariation(state.rmsValues);
  return {
    capture_available:Boolean(state.captureAvailable&&state.responseCount>0&&state.speechFrames>=10),
    listening_seconds:Number((state.listeningFrames*.1).toFixed(1)),
    speech_seconds:Number((state.speechFrames*.1).toFixed(1)),
    silence_ratio:totalFrames?Number((state.quietFrames/totalFrames).toFixed(3)):null,
    pitch_median_hz:pitchMedian?Number(pitchMedian.toFixed(1)):null,
    pitch_variation:pitchVariation==null?null:Number(Math.min(2,pitchVariation).toFixed(3)),
    volume_variation:volumeVariation==null?null:Number(Math.min(5,volumeVariation).toFixed(3)),
    word_count:state.wordCount,
    response_count:state.responseCount
  };
}

function startVoicePractice(){
  if(!activeConversationId||!$("#voice-practice-mode").checked)return;
  const SpeechRecognitionAPI=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!SpeechRecognitionAPI){$("#voice-status").textContent="Automatic voice practice needs Chrome/Edge speech recognition. You can continue by typing your answers.";$("#voice-status").classList.add("error");setSessionStatus("Voice recognition is not supported here. You can still type answers and generate a report.",true);return;}
  if(!("speechSynthesis" in window)){setSessionStatus("Spoken playback is not supported here. You can still type answers and generate a report.",true);return;}
  voicePracticeRunning=true;
  pendingVoiceTranscript="";
  startVoiceMetricsCapture();
  speakPracticeText(lastAssistantText,true);
}

function stopVoicePractice(){
  voiceMicWindowActive=false;
  voicePracticeRunning=false;
  voiceReplyPending=false;
  pendingVoiceTranscript="";
  voiceSpeechToken++;
  if(recognition){const current=recognition;recognition=null;try{current.stop()}catch{}}
  if("speechSynthesis" in window)window.speechSynthesis.cancel();
  $("#voice-start").classList.remove("hidden");
  $("#voice-stop").classList.add("hidden");
  $("#speech-pause").textContent="Pause";
  $("#voice-status").textContent="Voice practice stopped. Preparing your report…";
}

function startVoicePracticeListening(){
  const SpeechRecognitionAPI=window.SpeechRecognition||window.webkitSpeechRecognition;
  if(!voicePracticeRunning||!activeConversationId||voiceReplyPending||recognition||!SpeechRecognitionAPI)return;
  try{
    const instance=new SpeechRecognitionAPI();
    recognition=instance;
    pendingVoiceTranscript="";
    instance.lang=({auto:"en-IN",en:"en-IN",es:"es-ES",fr:"fr-FR",hi:"hi-IN",hinglish:"hi-IN",mr:"mr-IN"})[$("#chat-language").value]||"en-IN";
    instance.continuous=false;
    instance.interimResults=false;
    instance.maxAlternatives=1;
    instance.onstart=()=>{if(recognition!==instance)return;voiceMicWindowActive=true;$("#voice-status").textContent="Listening… speak naturally. Cognito does not store raw recordings; your browser’s speech-recognition service may process audio separately.";$("#voice-status").classList.remove("error");$("#voice-start").classList.add("hidden");$("#voice-stop").classList.remove("hidden")};
    instance.onresult=event=>{const result=event.results?.[event.results.length-1];if(result&&result.isFinal){pendingVoiceTranscript=(result[0]?.transcript||"").trim()}};
    instance.onerror=event=>{if(recognition!==instance)return;if(event.error==="not-allowed"||event.error==="service-not-allowed"){$("#voice-status").textContent="Microphone permission was denied. Allow microphone access or type your answer.";$("#voice-status").classList.add("error");voicePracticeRunning=false}else if(event.error!=="no-speech"){$("#voice-status").textContent=`Voice input issue (${event.error}). You can type your answer instead.`;$("#voice-status").classList.add("error")}};
    instance.onend=()=>{
      voiceMicWindowActive=false;
      if(recognition===instance)recognition=null;
      $("#voice-start").classList.remove("hidden");$("#voice-stop").classList.add("hidden");
      const transcript=pendingVoiceTranscript;pendingVoiceTranscript="";
      if(transcript){voiceDeliveryState.wordCount+=transcript.trim().split(/\s+/).filter(Boolean).length;voiceDeliveryState.responseCount++}
      if(transcript&&voicePracticeRunning){submitVoicePracticeAnswer(transcript);return}
      if(voicePracticeRunning&&!voiceReplyPending)setTimeout(startVoicePracticeListening,450);
    };
    instance.start();
  }catch(error){recognition=null;$("#voice-status").textContent="The microphone could not start. Check browser permission or type your answer.";$("#voice-status").classList.add("error");voicePracticeRunning=false;setSessionStatus("Voice input could not start. You can continue by typing.",true)}
}

async function submitVoicePracticeAnswer(transcript){
  if(!voicePracticeRunning||!activeConversationId||voiceReplyPending)return;
  voiceReplyPending=true;$("#end-session").disabled=true;
  $("#voice-status").textContent=`Heard: “${transcript.slice(0,110)}${transcript.length>110?"…":""}” · sending your answer…`;
  const ok=await sendMessage(transcript);
  voiceReplyPending=false;$("#end-session").disabled=!activeConversationId;
  if(!voicePracticeRunning)return;
  if(ok)speakPracticeText(lastAssistantText,true);
  else{voicePracticeRunning=false;setSessionStatus("Could not send that spoken answer. Retry from the message box, or stop to generate the report.",true)}
}

$("#speak-last").addEventListener("click",()=>speakPracticeText(lastAssistantText,false));
$("#speech-pause").addEventListener("click",()=>{if(!("speechSynthesis"in window))return;if(window.speechSynthesis.paused){window.speechSynthesis.resume();$("#speech-pause").textContent="Pause"}else if(window.speechSynthesis.speaking){window.speechSynthesis.pause();$("#speech-pause").textContent="Resume"}});
$("#speech-stop").addEventListener("click",()=>{voiceSpeechToken++;if("speechSynthesis"in window)window.speechSynthesis.cancel();$("#speech-pause").textContent="Pause";$("#voice-status").textContent="Spoken playback stopped.";if(voicePracticeRunning)setTimeout(startVoicePracticeListening,250)});
$("#chat-language").addEventListener("change",async event=>{if(!currentUser)return;const language=event.target.value;try{const profile=await api("/profile",{method:"PUT",body:JSON.stringify({display_name:currentUser.display_name,preferred_language:language})});setAccount(profile);showToast("Your response-language preference is saved.")}catch(error){event.target.value=currentUser.preferred_language||"auto";showToast(error.message)}});
document.addEventListener("click",event=>{const suggestion=event.target.closest(".suggestion");if(suggestion)sendMessage(suggestion.dataset.message)});
$("#clear-chat").addEventListener("click",async()=>{if(activeConversationId)await endPractice();standaloneMessages=[];viewingSavedConversation=false;showTranscript([{role:"assistant",content:"Conversation cleared. Ask me anything to start a new conversation, or search the learning library."}]);$("#insight-empty").classList.remove("hidden");$("#insight-content").classList.add("hidden")});

function renderResources(items){if(!items.length){$("#search-results").innerHTML='<div class="progress-empty"><strong>No guides found yet.</strong><span>Try a shorter phrase or browse all topics.</span></div>';return}$("#search-results").innerHTML=items.map(item=>`<article class="resource-card"><div class="resource-card-meta"><span class="tag violet-tag">${safeText(item.category||"Learning guide")}</span><span>${safeText(item.level||"")}</span></div><h3>${safeText(item.title)}</h3><p>${safeText(item.summary)}</p><button type="button" class="text-link" data-open-resource="${safeText(item.id)}">Open guide <span>↗</span></button></article>`).join("")}
async function loadLibrary(){const data=await api("/library");allResources=data.resources;$("#library-count").textContent=`${allResources.length} GUIDES`;const levels=[...new Set(data.levels)];$("#level-filter").innerHTML='<option value="">Any level</option>'+levels.map(level=>`<option value="${safeText(level)}">${safeText(level)}</option>`).join("");renderResources(allResources);return data}
async function runSearch(event){event?.preventDefault();const query=$("#resource-query").value.trim(),level=$("#level-filter").value;$("#search-button").disabled=true;$("#search-results").innerHTML='<div class="search-placeholder">Finding useful guides…</div>';try{if(!query){renderResources(allResources.filter(item=>!level||item.level===level));return}const params=new URLSearchParams({q:query,limit:"12"});if(level)params.set("level",level);const data=await api(`/library/search?${params}`);renderResources(data.results)}catch(error){$("#search-results").innerHTML=`<div class="form-message error">${safeText(error.message)} <button class="text-link" id="retry-search" type="button">Try again</button></div>`}finally{$("#search-button").disabled=false}}
$("#search-form").addEventListener("submit",runSearch);
$("#level-filter").addEventListener("change",()=>runSearch());
$("#resource-query").addEventListener("input",event=>{if(!event.target.value.trim()){const level=$("#level-filter").value;renderResources(allResources.filter(item=>!level||item.level===level))}});

function openLesson(data,question=null){activeLessonId=data.id;activeQuestionId=question?.question_id||`${data.id}-default`;seenExerciseIds=new Set([activeQuestionId]);awaitingNextQuestion=false;$("#lesson-meta").textContent=`${data.category} · ${data.level} · ${data.language.toUpperCase()}`;$("#lesson-title").textContent=data.title;$("#lesson-summary").textContent=data.summary;$("#lesson-content").textContent=data.content;$("#exercise-label").textContent=question?.exercise||data.exercise;$("#exercise-answer").value="";$("#exercise-answer").disabled=false;$("#assessment-result").textContent="";$("#assessment-result").className="form-message";const submit=$("#assessment-form button[type=submit]");submit.textContent="Check my answer ↗";$("#lesson-related").innerHTML=(data.related||[]).length?'<div class="mini-section-label">YOU MAY ALSO LIKE</div>'+data.related.slice(0,3).map(item=>`<button type="button" class="guide-chip" data-open-resource="${safeText(item.id)}">${safeText(item.title)}</button>`).join(""):"";$("#lesson-dialog").showModal()}
async function fetchLesson(id){try{const data=await api(`/library/${encodeURIComponent(id)}`);let question=null;try{question=await api(`/library/${encodeURIComponent(id)}/question`)}catch{}openLesson(data,question)}catch(error){showToast(error.message)}}
async function loadNextExercise(){if(!activeLessonId)return;const button=$("#assessment-form button[type=submit]"),input=$("#exercise-answer"),result=$("#assessment-result");button.disabled=true;button.textContent="Loading question…";try{const params=new URLSearchParams();if(seenExerciseIds.size)params.set("exclude",Array.from(seenExerciseIds).join(","));const question=await api(`/library/${encodeURIComponent(activeLessonId)}/question?${params.toString()}`);activeQuestionId=question.question_id;seenExerciseIds.add(activeQuestionId);$("#exercise-label").textContent=question.exercise;input.value="";input.disabled=false;result.textContent="";result.className="form-message";awaitingNextQuestion=false;button.textContent="Check my answer ↗";input.focus()}catch(error){result.textContent=error.message;result.className="form-message error";button.textContent="Try next question ↗"}finally{button.disabled=false}}
document.addEventListener("click",event=>{const open=event.target.closest("[data-open-resource]");if(open)fetchLesson(open.dataset.openResource);if(event.target.closest("#topic-open")&&currentTopicId)fetchLesson(currentTopicId);if(event.target.closest("#retry-search"))runSearch()});
$("#assessment-form").addEventListener("submit",async event=>{event.preventDefault();if(!activeLessonId)return;if(awaitingNextQuestion){await loadNextExercise();return}const button=event.currentTarget.querySelector("button");button.disabled=true;const result=$("#assessment-result");result.className="form-message";result.textContent="Checking your answer…";try{const data=await api(`/library/${encodeURIComponent(activeLessonId)}/assessment`,{method:"POST",body:JSON.stringify({answer:$("#exercise-answer").value,question_id:activeQuestionId})});result.textContent=data.feedback;result.className="form-message "+(data.score===1?"success":data.score>0?"notice":"error");if(currentUser){await loadProgress();if(data.score===1)await loadRecommendations()}awaitingNextQuestion=true;$("#exercise-answer").disabled=true;button.textContent="Next question ↗"}catch(error){result.textContent=error.message;result.className="form-message error"}finally{button.disabled=false}});

function renderProgress(data){const root=$("#progress-content");if(!currentUser){root.innerHTML='<div class="progress-empty"><strong>Every journey starts somewhere.</strong><span>Complete a practice question to see it here. Sign in to save your progress across sessions.</span><button class="text-link" id="progress-signin" type="button">Sign in or create an account ↗</button></div>';return}const completed=data.completed||[],recent=data.recent_attempts||[];root.innerHTML=`<div class="progress-stats"><div><strong>${completed.length}</strong><span>GUIDES COMPLETED</span></div><div><strong>${recent.length}</strong><span>RECENT PRACTICE</span></div></div>${completed.length?`<div class="completed-list">${completed.slice(0,5).map(item=>`<button type="button" class="completed-item" data-open-resource="${safeText(item.resource.id)}"><span>${safeText(item.resource.title)}</span><b>${Math.round((item.score||0)*100)}%</b></button>`).join("")}</div>`:'<div class="progress-empty compact"><strong>Your first completed guide will appear here.</strong><span>Choose a guide from the library and try its practice question.</span></div>'}${recent.length?`<div class="recent-list"><div class="mini-section-label">RECENT PRACTICE</div>${recent.map(item=>`<button type="button" class="recent-item" data-open-resource="${safeText(item.resource_id)}" style="cursor:pointer;width:100%"><span>${safeText(item.title)}</span><b>${Math.round(item.score*100)}%</b></button>`).join("")}</div>`:""}`}
async function loadProgress(){if(!currentUser){renderProgress({});return}try{renderProgress(await api("/progress"))}catch(error){$("#progress-content").innerHTML=`<div class="form-message error">${safeText(error.message)}</div>`}}
async function loadRecommendations(){try{const data=await api("/recommendations?limit=4");$("#recommendation-list").innerHTML=data.recommendations.map(item=>`<article class="recommendation-item"><div><span class="tag green-tag">${safeText(item.level)} · ${safeText(item.category)}</span><h3>${safeText(item.title)}</h3><p>${safeText(item.summary)}</p><button class="text-link" type="button" data-open-resource="${safeText(item.id)}">Take a look ↗</button></div><div class="feedback-actions" aria-label="Rate this suggestion"><button type="button" title="Helpful" aria-label="Helpful suggestion" data-feedback="${safeText(item.id)}" data-helpful="true">↑</button><button type="button" title="Not for me" aria-label="Not for me" data-feedback="${safeText(item.id)}" data-helpful="false">↓</button></div></article>`).join("")||'<div class="search-placeholder">No suggestions yet.</div>'}catch(error){$("#recommendation-list").innerHTML=`<div class="form-message error">${safeText(error.message)}</div>`}}
document.addEventListener("click",async event=>{const feedback=event.target.closest("[data-feedback]");if(!feedback)return;if(!currentUser){showToast("Sign in to personalize your suggestions.");openAccount();return}feedback.disabled=true;try{await api(`/library/${encodeURIComponent(feedback.dataset.feedback)}/feedback`,{method:"POST",body:JSON.stringify({helpful:feedback.dataset.helpful==="true"})});await loadRecommendations();showToast("Thanks — your suggestions are updated.")}catch(error){showToast(error.message);feedback.disabled=false}});

function setAccountMode(mode){accountMode=mode;const registering=mode==="register";$("#account-title").textContent=registering?"Create your account":"Welcome back";$("#account-submit").textContent=registering?"Create account":"Sign in";$("#name-field").classList.toggle("hidden",!registering);$("#account-name").required=registering;$("#account-password").autocomplete=registering?"new-password":"current-password";$("#auth-switch").textContent=registering?"Already have an account? Sign in":"New here? Create an account";$("#account-message").textContent=""}
function openAccount(){if(currentUser){$("#account-title").textContent=`Hello, ${currentUser.display_name}`;$("#dialog-copy")?.classList.add("hidden");$("#account-form").classList.add("hidden");$("#profile-form").classList.remove("hidden");$("#profile-name").value=currentUser.display_name;$("#profile-language").value=currentUser.preferred_language||"auto";$("#auth-switch").classList.add("hidden");$("#signout-button")?.classList.remove("hidden");$("#account-dialog").showModal();return}$("#dialog-copy")?.classList.remove("hidden");$("#account-form").classList.remove("hidden");$("#profile-form").classList.add("hidden");$("#auth-switch").classList.remove("hidden");$("#signout-button")?.classList.add("hidden");setAccountMode("login");$("#account-dialog").showModal()}
$("#account-button").addEventListener("click",openAccount);document.addEventListener("click",event=>{if(event.target.closest("#progress-signin"))openAccount()});
$("#auth-switch").addEventListener("click",()=>setAccountMode(accountMode==="login"?"register":"login"));
$("#account-form").addEventListener("submit",async event=>{event.preventDefault();const button=$("#account-submit"),message=$("#account-message");button.disabled=true;message.textContent="";const body=accountMode==="register"?{email:$("#account-email").value,display_name:$("#account-name").value,password:$("#account-password").value}:{email:$("#account-email").value,password:$("#account-password").value};try{const data=await api(`/auth/${accountMode}`,{method:"POST",body:JSON.stringify(body)});csrfToken=data.csrf_token;setAccount(data.user);$("#account-dialog").close();await Promise.all([loadProgress(),loadRecommendations(),loadConversationHistory()]);showToast(accountMode==="register"?"Your account is ready.":"You're signed in.")}catch(error){message.textContent=error.message;message.className="form-message error"}finally{button.disabled=false}});
$("#profile-form").addEventListener("submit",async event=>{event.preventDefault();const button=event.currentTarget.querySelector("button");button.disabled=true;try{const profile=await api("/profile",{method:"PUT",body:JSON.stringify({display_name:$("#profile-name").value,preferred_language:$("#profile-language").value})});setAccount(profile);$("#account-dialog").close();showToast("Your profile has been updated.")}catch(error){$("#account-message").textContent=error.message;$("#account-message").className="form-message error"}finally{button.disabled=false}});
$("#signout-button")?.addEventListener("click",async()=>{try{await api("/auth/logout",{method:"POST"})}catch{}currentUser=null;activeConversationId=null;viewingSavedConversation=false;voiceReplyPending=false;pendingVoiceTranscript="";voiceSpeechToken++;voiceDeliveryState=createVoiceDeliveryState();setPracticeActive(false);showTranscript([{role:"assistant",content:"You are signed out. Sign in to access your saved practice sessions."}]);await loadSession();$("#account-dialog").close();await Promise.all([loadProgress(),loadRecommendations(),loadConversationHistory()]);showToast("You are signed out.")});

function renderReport(report){const dialog=$("#report-dialog");const mode=MODE_LABELS[report.session_type]||report.session_type||"Practice";$("#report-session-meta").textContent=`${mode} · Generated ${report.generated_at?new Date(report.generated_at).toLocaleString():"just now"}`;const score=report.overall_score;$("#report-score-value").textContent=score!=null?score:"—";const pct=score!=null?Math.max(0,Math.min(100,score)):0;const ring=$("#report-score-ring");ring.style.background=score!=null?`conic-gradient(#7159e8 0%,#50ba95 ${pct}%,#e8e9e5 ${pct}%)`:'conic-gradient(#e8e9e5 0%,#e8e9e5 100%)';$("#report-score-summary").textContent=report.score_summary||"";const notice=$("#report-evidence-notice");if(report.insufficient_evidence||report.evidence_limitations){notice.textContent=report.evidence_limitations||"This session may not have enough data for a complete evaluation.";notice.classList.remove("hidden")}else{notice.classList.add("hidden")}const voice=report.spoken_confidence||null;const voiceSection=$("#report-voice-confidence");const voiceContent=$("#report-voice-confidence-content");voiceSection.classList.toggle("hidden",!voice);if(voice){const fmtScore=value=>value==null?"Not measured":`${safeText(value)}/100`;const fmtWpm=value=>value==null?"Not measured":`${safeText(value)} words/min`;const metrics=[{label:"Transcript clarity & fluency",value:fmtScore(voice.clarity_fluency_score)},{label:"Conversational pace",value:voice.speaking_pace_wpm==null?"Not measured":fmtWpm(voice.speaking_pace_wpm)},{label:"Vocal tone variation",value:fmtScore(voice.vocal_tone_score)},{label:"Pause balance",value:fmtScore(voice.pause_balance_score)},{label:"Volume steadiness",value:fmtScore(voice.volume_steadiness_score)}];const listHtml=items=>Array.isArray(items)&&items.length?`<ul class="voice-confidence-list">${items.map(item=>`<li>${safeText(item)}</li>`).join("")}</ul>`:"";voiceContent.innerHTML=`<div class="voice-confidence-panel"><div class="voice-confidence-top"><div class="voice-confidence-score"><span class="voice-confidence-number">${voice.score==null?"—":safeText(voice.score)}</span><span class="voice-confidence-outof">${voice.score==null?"NOT MEASURED":"/100"}</span></div><div class="voice-confidence-intro"><strong>${safeText(voice.label||"Spoken confidence estimate")}</strong><p>${safeText(voice.summary||"")}</p></div></div><div class="voice-confidence-grid">${metrics.map(metric=>`<div class="voice-confidence-metric"><span>${safeText(metric.label)}</span><strong>${metric.value}</strong></div>`).join("")}</div>${voice.clarity_fluency_summary?`<p class="voice-confidence-note">${safeText(voice.clarity_fluency_summary)}</p>`:""}${voice.voice_strengths?.length?`<div class="voice-confidence-subtitle">Delivery strengths</div>${listHtml(voice.voice_strengths)}`:""}${voice.clarity_fluency_strengths?.length?`<div class="voice-confidence-subtitle">Speaking strengths</div>${listHtml(voice.clarity_fluency_strengths)}`:""}${voice.suggestions?.length||voice.clarity_fluency_improvements?.length?`<div class="voice-confidence-subtitle">Try next</div>${listHtml([...(voice.suggestions||[]),...(voice.clarity_fluency_improvements||[])])}`:""}<p class="voice-confidence-note">${safeText(voice.limitations||"Delivery scores are estimates, not a psychological assessment.")}</p></div>`;}const cats=report.category_scores||[];$("#report-categories").classList.toggle("hidden",!cats.length);$("#report-category-list").innerHTML=cats.map(c=>`<div class="category-item"><div class="category-item-header"><span class="category-item-name">${safeText(c.name||"Category")}</span><span class="category-item-score">${c.score!=null?c.score:"—"}</span></div><div class="category-bar"><div class="category-bar-fill" style="width:${c.score!=null?c.score:0}%"></div></div>${c.explanation?`<div class="category-item-detail">${safeText(c.explanation)}</div>`:""} ${c.suggestion?`<div class="category-item-suggestion">${safeText(c.suggestion)}</div>`:""}</div>`).join("");const strengths=report.strengths||[];$("#report-strengths").classList.toggle("hidden",!strengths.length);$("#report-strength-list").innerHTML=strengths.map(s=>`<div class="strength-card"><div class="strength-card-title">${safeText(s.title||"Strength")}</div><div class="strength-card-body">${safeText(s.explanation||"")}</div>${s.example?`<div class="strength-card-quote">${safeText(s.example)}</div>`:""} ${s.why_effective?`<div class="strength-card-body">${safeText(s.why_effective)}</div>`:""}</div>`).join("");const improvements=report.improvements||[];$("#report-improvements").classList.toggle("hidden",!improvements.length);$("#report-improvement-list").innerHTML=improvements.map(i=>`<div class="improvement-card"><div class="improvement-card-title">${safeText(i.issue||"Area to improve")}</div>${i.excerpt?`<div class="improvement-card-excerpt">${safeText(i.excerpt)}</div>`:""} ${i.improved_version?`<div class="improvement-card-improved">✓ ${safeText(i.improved_version)}</div>`:""} ${i.why_better?`<div class="improvement-card-body">${safeText(i.why_better)}</div>`:""} ${i.exercise?`<div class="improvement-card-exercise">Practice: ${safeText(i.exercise)}</div>`:""}</div>`).join("");const rewrites=report.answer_rewrites||[];$("#report-rewrites").classList.toggle("hidden",!rewrites.length);$("#report-rewrite-list").innerHTML=rewrites.map(r=>`<div class="rewrite-card"><div class="rewrite-card-label">YOUR ANSWER</div><div class="rewrite-card-original">${safeText(r.original||"")}</div><div class="rewrite-card-label">IMPROVED VERSION</div><div class="rewrite-card-improved">${safeText(r.improved||"")}</div>${r.reason?`<div class="rewrite-card-reason">${safeText(r.reason)}</div>`:""} ${r.learning_point?`<div class="rewrite-card-body">${safeText(r.learning_point)}</div>`:""}</div>`).join("");const next=report.recommended_next_practice;$("#report-next").classList.toggle("hidden",!next);if(next){$("#report-next-content").innerHTML=`<div class="next-practice-mode">${safeText(MODE_LABELS[next.mode]||next.mode||"Practice")}</div><div class="next-practice-focus">${safeText(next.focus||"")}</div>${next.reason?`<div class="next-practice-reason">${safeText(next.reason)}</div>`:""}`}$("#report-rubric").textContent=`Rubric v${report.rubric_version||"1.0"} · ${report.spoken_confidence?.voice_sample_available?"Transcript + local delivery estimate":"Transcript-based; voice delivery not measured"}`;dialog.showModal()}
$("#report-close-btn").addEventListener("click",()=>$("#report-dialog").close());
async function bootstrap(){try{await loadSession();await loadLibrary();await Promise.all([loadProgress().catch(()=>null),loadRecommendations().catch(()=>null),loadConversationHistory().catch(()=>null)]);const health=await api("/health");setStatus(health.status==="ok"?"Ready":"Check service",health.status==="ok");if(health.tutoring==="configured"){$("#provider-disclosure").textContent="Standalone assistant messages are not saved. Signed-in practice transcripts are sent to your configured AI provider and saved to your account. Cognito does not upload or store raw audio recordings; your browser’s speech-recognition service may process audio separately. Only aggregate delivery metrics are included in the report request.";$("#provider-disclosure").classList.remove("hidden")}}catch(error){setStatus("Service offline",false);$("#library-count").textContent="UNAVAILABLE";$("#search-results").innerHTML=`<div class="form-message error">${safeText(error.message)}</div>`;showToast(error.message)}}
const observed=[...document.querySelectorAll("#chat,#library,#progress")];const navLinks=[...document.querySelectorAll(".side-nav a")];const observer=new IntersectionObserver(entries=>{const visible=entries.filter(entry=>entry.isIntersecting).sort((a,b)=>b.intersectionRatio-a.intersectionRatio)[0];if(!visible)return;navLinks.forEach(link=>link.classList.toggle("active",link.hash===`#${visible.target.id}`));const label=navLinks.find(link=>link.hash===`#${visible.target.id}`);if(label)$("#breadcrumb-page").textContent=label.textContent.trim()},{rootMargin:"-20% 0px -65% 0px",threshold:[0,.2,.5]});observed.forEach(section=>observer.observe(section));
bootstrap();
