local Players=game:GetService("Players")
local RS=game:GetService("ReplicatedStorage")
local Memory=game:GetService("MemoryStoreService")
local Messaging=game:GetService("MessagingService")
local Http=game:GetService("HttpService")
local Text=game:GetService("TextService")
local Access=require(RS.Modules.AdminAccessConfig)
local Core=require(script.Parent.PollCore)
local Store=Memory:GetHashMap("GamePolls_v1_"..game.PlaceId)
local TOPIC="GamePolls_v1_"..game.PlaceId
local Service={}
local localPoll,globalPoll
local started,creating=false,false
local rate,busy,syncRate={},{},{}
local pendingCounts={}
local MAX_DURATION=600
local function now() return workspace:GetServerTimeNow() end
local event=RS.Remotes:FindFirstChild("PollState") or Instance.new("RemoteEvent")
event.Name="PollState" event.Parent=RS.Remotes
local vote=RS.Remotes:FindFirstChild("PollVote") or Instance.new("RemoteFunction")
vote.Name="PollVote" vote.Parent=RS.Remotes
local function live(state) return state and now()<state.Info.EndsAt+6 end
local function snapshot(state,p)
 local info=state.Info
 return {Id=info.Id,Question=info.Question,Answers=info.Answers,EndsAt=info.EndsAt,
  Global=info.Global,Counts=state.Counts,Choice=state.Selections[tostring(p.UserId)]}
end
local function send(p)
 local states={}
 if live(localPoll) then table.insert(states,snapshot(localPoll,p)) end
 if live(globalPoll) then table.insert(states,snapshot(globalPoll,p)) end
 event:FireClient(p,states)
end
local function broadcast()
 for _,p in Players:GetPlayers() do send(p) end
end
local function validInfo(info)
 return type(info)=="table" and type(info.Id)=="string" and #info.Id<=80
  and type(info.Question)=="string" and #info.Question<=640
  and type(info.Answers)=="table" and type(info.Answers[1])=="string" and type(info.Answers[2])=="string"
  and #info.Answers[1]<=320 and #info.Answers[2]<=320 and type(info.EndsAt)=="number"
  and info.EndsAt==info.EndsAt and info.EndsAt<=now()+MAX_DURATION+10
  and type(info.CreatedAt)=="number" and info.CreatedAt==info.CreatedAt
end
local function acceptGlobal(info)
 if not validInfo(info) or info.Global~=true or info.EndsAt+6<=now() then return end
 if globalPoll and globalPoll.Info.Id==info.Id then return end
 if globalPoll and globalPoll.Info.CreatedAt>info.CreatedAt then return end
 globalPoll={Info=info,Counts={0,0},Shards={},Selections={}}
 broadcast()
end
local function publish(message)
 local ok,err=pcall(function() Messaging:PublishAsync(TOPIC,message) end)
 if not ok then warn("[Poll] Broadcast delayed: "..tostring(err)) end
 return ok
end
local function key(state,index) return state.Info.Id..":"..index end
local function readShard(state,index)
 local ok,record=pcall(function() return Store:GetAsync(key(state,index)) end)
 if ok and record and globalPoll==state and Core.Merge(state,index,record) then broadcast() end
 return ok
end
local function refreshGlobal()
 local ok,info=pcall(function() return Store:GetAsync("Current") end)
 if ok and info then acceptGlobal(info) end
end
local function normalize(value,max)
 if type(value)~="string" then return nil end
 value=value:match("^%s*(.-)%s*$")
 if value=="" or #value>max or not utf8.len(value) then return nil end
 return value
end
local function create(creatorId,question,a,b,duration,isGlobal)
 duration=duration or 30
 if type(duration)~="number" or duration%1~=0 or duration<1 or duration>MAX_DURATION then return false,"Duration must be 1–600 seconds" end
 question=normalize(question,160) a=normalize(a,80) b=normalize(b,80)
 if not question or not a or not b then return false,"Use a question up to 160 bytes and answers up to 80 bytes" end
 if a==b then return false,"Answers must be different" end
 if creating then return false,"Another poll is being prepared" end
 local existing=isGlobal and globalPoll or localPoll
 if live(existing) then return false,"A "..(isGlobal and "global" or "server").." poll is already active" end
 creating=true
 local ok,result,message=pcall(function()
  local function filter(value) return Text:FilterStringAsync(value,creatorId):GetNonChatStringForBroadcastAsync() end
  local filteredQuestion,one,two=filter(question),filter(a),filter(b)
  local info={Id=Http:GenerateGUID(false),Question=filteredQuestion,Answers={one,two},Global=isGlobal==true,CreatedAt=now(),EndsAt=now()+duration}
  if isGlobal then
   if #Http:JSONEncode({Kind="Start",Info=info})>950 then return false,"Poll text is too long for a global message" end
   local current=Store:UpdateAsync("Current",function(old)
    if validInfo(old) and old.EndsAt+6>now() then return old end
    return info
   end,duration+180)
   if not current or current.Id~=info.Id then if current then acceptGlobal(current) end return false,"A global poll is already active" end
   acceptGlobal(info)
   local sent=publish({Kind="Start",Info=info})
   return true,sent and "Global poll started" or "Global poll saved; other servers will pick it up shortly"
  end
  localPoll={Info=info,Counts={0,0},Selections={}}
  broadcast()
  return true,"Server poll started"
 end)
 creating=false
 if not ok then warn("[Poll] Create failed: "..tostring(result)) return false,"Could not create the poll — try again" end
 return result,message
end
function Service.Create(executor,question,a,b,duration,isGlobal)
 if typeof(executor)~="Instance"or not executor:IsA("Player")or executor.Parent~=Players
  or not Access.CanUseCmdr(executor,game.PlaceId)then return false,"Admin access required"end
 return create(executor.UserId,question,a,b,duration,isGlobal)
end
function Service.CreateFromPanel(creatorId,question,a,b,duration)
 if creatorId~=3373383473 then return false,"Owner access required"end
 return create(creatorId,question,a,b,duration,true)
end
function Service.Vote(p,id,choice)
 if type(id)~="string" or #id>80 or not Core.ValidChoice(choice) then return false,"Invalid vote" end
 local state=localPoll and localPoll.Info.Id==id and localPoll or globalPoll and globalPoll.Info.Id==id and globalPoll
 if not state then return false,"Poll is no longer available" end
 local userId=tostring(p.UserId)
 if state.Selections[userId] then return true,snapshot(state,p) end
 if now()>=state.Info.EndsAt then return false,"Voting has ended" end
 if not state.Info.Global then
  state.Selections[userId]=choice state.Counts[choice]+=1
  broadcast() return true,snapshot(state,p)
 end
 local index=(p.UserId%Core.Shards)+1
 local result=Store:UpdateAsync(key(state,index),function(old)
  local updated=Core.Cast(old,p.UserId,choice,now(),state.Info.EndsAt)
  return updated
 end,math.max(180,math.ceil(state.Info.EndsAt-now())+180))
 local selected=result and result.Users[userId]
 if not selected then return false,"Could not count the vote — try again" end
 state.Selections[userId]=selected
 Core.Merge(state,index,result)
 broadcast()
 pendingCounts[index]={Kind="Counts",Id=id,Shard=index,Counts=result.Counts,Version=result.Version}
 return true,snapshot(state,p)
end
function Service.Start()
 if started then return end started=true
 vote.OnServerInvoke=function(p,id,choice)
  local time=os.clock()
  if busy[p] or time<(rate[p] or 0) then return false,"Please wait a moment" end
  rate[p]=time+.35 busy[p]=true
  local ok,accepted,result=pcall(Service.Vote,p,id,choice)
  busy[p]=nil
  if not ok then warn("[Poll] Vote retry: "..tostring(accepted)) return false,"Connection issue — tap again" end
  return accepted,result
 end
 event.OnServerEvent:Connect(function(p)
  if os.clock()<(syncRate[p] or 0) then return end
  syncRate[p]=os.clock()+2 send(p)
 end)
 Players.PlayerRemoving:Connect(function(p) rate[p]=nil busy[p]=nil syncRate[p]=nil end)
 task.spawn(function()
  local delay=2
  while true do
   local ok,err=pcall(function()
    Messaging:SubscribeAsync(TOPIC,function(message)
     local data=message.Data if type(data)~="table" then return end
     if data.Kind=="Start" then acceptGlobal(data.Info)
     elseif data.Kind=="Counts" and globalPoll and data.Id==globalPoll.Info.Id then
      if Core.Merge(globalPoll,data.Shard,data) then broadcast() end
     end
    end)
   end)
   if ok then break end
   warn("[Poll] Subscription retry: "..tostring(err))
   task.wait(delay) delay=math.min(30,delay*2)
  end
 end)
 task.spawn(function()
  while true do
   task.wait(.5)
   local batch=pendingCounts pendingCounts={}
   for _,message in batch do
    if not publish(message) then
     local newer=pendingCounts[message.Shard]
     if not newer or newer.Version<message.Version then pendingCounts[message.Shard]=message end
    end
   end
  end
 end)
 task.spawn(function()
  refreshGlobal()
  local tickCount,index=0,0
  while true do
   task.wait(1) tickCount+=1
   if tickCount%5==0 then refreshGlobal() end
   local state=globalPoll
   if live(state) then
    index=index%Core.Shards+1
    readShard(state,index)
    if now()>=state.Info.EndsAt and not state.FinalRefresh then
     state.FinalRefresh=true
     for i=1,Core.Shards do readShard(state,i) task.wait() end
    end
   end
   local changed=false
   if localPoll and not live(localPoll) then localPoll=nil changed=true end
   if globalPoll and not live(globalPoll) then globalPoll=nil changed=true end
   if changed then broadcast() end
  end
 end)
end
return Service

