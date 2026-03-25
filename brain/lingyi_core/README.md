lingyi_core会接收：
1. session key：频道（确保不同的频道分配给独立的分身切片，不会有信息混淆），频道内置聊天记录，并通过一个标记区分历史消息和新消息。lingyi_core可以直接从频道提取历史消息和新消息，并在提取后更新标记。
2. caller message：消息的来源的备注（比如是主聊天界面，是QQ，是其他的什么地方）
3. prompt：对应的行动指南（比如主聊天界面是无条件响应用户的要求，QQ群聊和私聊则是有着不同的响应规则）
4. keyword list：供给记忆系统的额外关键词列表

lingyi_core会自主寻找：
1. core prompt（尚未实装）：铃依的主人格prompt，规定了铃依的发言风格，爱好等。
2. tool list：工具箱（会有一个主工具箱，以及其下各种功能的，如QQ工具箱分支之类的。mcp和agent都会伪装成tools在这里）
3. memory：根据session为自己配置一个对应的记忆模块（主要是为了避免重复记忆）。AI可以通过这个查询长期记忆（短期上下文依赖外部提供的history）。该memory同时应该在lingyi_core储存一个临时记忆库，储存最近三次提取到的长期记忆（node&relation），去重并在通讯时提交给模型。
4. activity tracker：反馈当前是否有工具正在运行，有哪些工具被调用了但是尚未返回结果。
5. input buffer：虽然在不同的session中，铃依是用的并发进程，但单个session中铃依应该是单一的。接收到消息之后->程序将信息整合提供给模型->模型返回发言or调用必须的工具->工具执行，模型等待：模型思考阶段，用户所发送的所有消息都将进入input_buffer，并在模型返回工具调用，等待工具调用返回时被释放给模型。供模型决定是否要继续等待工具执行的结果，或是立即终止工具的调用。

最终递交给大模型的信息：
1. core prompt + prompt
2. memory
3. tool list
4. activity tracker
5. tool result
6. history + new message

模型的输出：
1. message：消息（QQ的接口会丢弃message部分，不过应该不需要在lingyi_core做任何调整）
2. tool_call：这里有一部分特殊的tool call：cancel取消某一个正在进行的tool，end终止所有结束当前轮次。tool按照循序逐个执行（并发），如果先执行再end那就是终止此前的所有tool call，清空缓存，重置工具调用循环计数器。然后以新的循环执行end后的tool call。

工作流程：
0. 信息输入buffer，等待2-5s（避免用户快速连续发言）后打包输入（持续运行）
1. 所有的信息汇总之后交给大模型。
2. 大模型思考，新信息进入buffer但禁止输入给模型
3. 大模型输出message和tool call，等待新消息
4. buffer输入进来的内容（如果有）跟随tool call一起输入，或者等待至多1s如果tool call无回应直接输入。
5. buffer & tool prograss都是空的则无需任何行动，否则重复1-4


考虑加入但尚未确定如何实装：
1. work list：工作计划，应当是一个recursive tree的样子，记录[{work_id: "id",work_name:"name",decription:"decription",sub_work: [work list]",status:"complete/canceled/in process/waiting"}]。目的是让AI知道自己正在做什么，将要作什么。但是何时书写worklist，如何清除已经完成的任务，对于插入的临时任务要如何处理，尚未确定。