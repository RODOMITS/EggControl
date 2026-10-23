local Http=game:GetService("HttpService")
local Players=game:GetService("Players")
local Messaging=game:GetService("MessagingService")
local Memory=game:GetService("MemoryStoreService")
local Run=game:GetService("RunService")
local SS=game.ServerStorage
local Services=SS.Services
local S={}
local started=false
local OWNER=3373383473
local PLACE=80549689028789
local TOPIC="EggControl_v2"
local Claims=Memory:GetSortedMap("EggControlClaims_v2")
local Results=Memory:GetSortedMap("EggControlResults_v2")
local Live=Memory:GetSortedMap("EggControlLive_v2")
local targetActions={fakeupgrade=true,fakedrop=true,fakegrab=true,unfakegrab=true,isplayeronline=true,remoteannounce=true}
local globalActions={announcement=true,globalevent=true,globalmorph=true,globalunmorph=true,globalgive=true,globalgivecandy=true,globalskipfuse=true,globalpoll=true,globalcode=true}
local function escaped(text)return text:gsub("&","&amp;"):gsub("<","&lt;"):gsub(">","&gt;")end
local function text(value,max)
 assert(type(value)=="string"and #value>0 and #value<=max,"Invalid text")
 return value
end
local function integer(value,low,high)
 assert(type(value)=="number"and value==value and value%1==0 and value>=low and value<=high,"Invalid number")
 return value
end
local function execute(d,player)
 local a=d.args local action=d.action
 if action=="fakeupgrade"then return true,require(Services.FakeUpgradeService).Arm(player)
 elseif action=="fakegrab"or action=="unfakegrab"then return require(Services.FakeGrabService).Set(player,action=="fakegrab")
 elseif action=="fakedrop"then return require(Services.FakeEggDrop).Queue(player,text(a.petname,200))
 elseif action=="isplayeronline"then return true,player.Name.." online | "..game.JobId
 elseif action=="remoteannounce"then
  local result=game:GetService("TextService"):FilterStringAsync(text(a.announce,400),OWNER,Enum.TextFilterContext.PublicChat)
  local value=result:GetNonChatStringForUserAsync(player.UserId)
  if player.Parent~=Players then return false,"Player left"end
  game.ReplicatedStorage.Remotes.ShowNotification:FireClient(player,escaped(value),"Info",8)
  return true,"Notification sent to "..player.Name
 elseif action=="announcement"then
  local result=game:GetService("TextService"):FilterStringAsync(text(a.message,500),OWNER,Enum.TextFilterContext.PublicChat)
  local value=result:GetNonChatStringForBroadcastAsync()
  local color="#"..require(game.ReplicatedStorage.Modules.GetNameColor)("rodomits"):ToHex()
  Messaging:PublishAsync("GlobalAnnouncement",{FinalString='<font color="'..color..'">rodomits</font>: '..escaped(value)})
  return true,"Announcement published"
 elseif action=="globalgive"then
  return require(Services.GlobalGive).Give(a.kind,a.name,integer(a.amount,1,20),a.mutation)
 elseif action=="globalgivecandy"then return require(Services.GlobalGive).Give("candy",nil,integer(a.amount,1,10000))
 elseif action=="globalmorph"then return require(Services.GlobalMorph).Start(text(a.petname,200),integer(a.duration,1,3600),a.withboard==true,a.slowdown==true)
 elseif action=="globalunmorph"then return require(Services.GlobalMorph).Stop()
 elseif action=="globalskipfuse"then
  local ok,count,err=require(Services.AdminFuseSkip).Global()
  return ok,ok and ("Global fuse skip sent; local: "..count)or tostring(err)
 elseif action=="globalcode"then
  return require(Services.RewardCodes).Create(text(a.name,32),integer(a.activations,1,10000),a.type,integer(a.amount,1,1e12),a.rewardname or "",true,OWNER)
 elseif action=="globalpoll"then
  return require(Services.PollService).CreateFromPanel(OWNER,text(a.question,160),text(a.answer1,80),text(a.answer2,80),integer(a.duration,1,600))
 elseif action=="globalevent"then
  assert(a.action=="start"or a.action=="stop","Invalid event action")
  local events=require(game.ServerScriptService.EventsSystem)
  assert(table.find(events.GetRegisteredNames(),a.name),"Unknown event")
  local duration=integer(a.duration,0,86400)
  local cmd=game.ServerScriptService.Cmdr.BuiltInCommands.DefaultAdmin.globaleventServer
  return true,require(cmd)({Executor={UserId=OWNER,Name="rodomits"}},a.action,a.name,duration)
 end
 return false,"Unsupported command"
end
function S.Receive(data)
 if type(data)=="string"then local ok,v=pcall(Http.JSONDecode,Http,data)if not ok then return false end data=v end
 if type(data)~="table"or data.owner~=OWNER or data.place~=PLACE or type(data.id)~="string"or #data.id~=32 or data.id:find("[^%da-f]")
  or type(data.at)~="number"or data.at~=data.at or math.abs(os.time()-data.at)>60 or type(data.args)~="table"then return false end
 local player
 if targetActions[data.action]then
  if type(data.target)~="number"then return false end
  player=Players:GetPlayerByUserId(data.target)
  if not player then return false end
 elseif not globalActions[data.action]then return false end
 local token=Http:GenerateGUID(false)
 local ok,claim=pcall(Claims.UpdateAsync,Claims,data.id,function(old)
  if old then return nil end
  return {Token=token,Job=game.JobId}
 end,3600)
 if not ok or not claim or claim.Token~=token then return false end
 -- Claim precedes execution: duplicated deliveries cannot repeat a giveaway.
 local success,applied,message=pcall(execute,data,player)
 local row={status=success and applied==true and "done"or "failed",result=success and tostring(message):sub(1,500)or "Command failed; see server log",at=os.time(),job=game.JobId}
 if not success then warn("[OpenCloudControl] "..tostring(applied))end
 for attempt=1,3 do
  if pcall(Results.SetAsync,Results,data.id,row,1800)then break end
  task.wait(attempt)
 end
 return true
end
local function number(p,name)
 local leader=p:FindFirstChild("leaderstats")
 local value=(leader and leader:FindFirstChild(name))or p:FindFirstChild(name.."Value")or p:FindFirstChild(name)
 local n=value and tonumber(value.Value)
 if n and n==n and n>=0 and n<=1e18 then return n end
end
function S.Start()
 if started or Run:IsStudio()or game.PlaceId~=PLACE then return end
 started=true
 task.spawn(function()
  while true do
   local ok=pcall(Messaging.SubscribeAsync,Messaging,TOPIC,function(message)S.Receive(message.Data)end)
   if ok then break end
   task.wait(15)
  end
 end)
 task.spawn(function()
  while true do
   local rows={}
   for _,p in Players:GetPlayers()do table.insert(rows,{id=p.UserId,name=p.Name,speed=number(p,"Speed"),money=number(p,"Money")})end
   local ok=pcall(Live.SetAsync,Live,game.JobId,{job=game.JobId,place=game.PlaceId,at=os.time(),players=rows},75,os.time())
   if not ok then warn("[OpenCloudControl] Live status unavailable")end
   task.wait(20)
  end
 end)
 game:BindToClose(function()pcall(Live.RemoveAsync,Live,game.JobId)end)
end
return S

