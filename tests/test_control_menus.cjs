// Polling must preserve touch targets; anchored menus must fit small viewports.
const assert=require('node:assert/strict'),fs=require('node:fs'),vm=require('node:vm'),path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../web/app.js'),'utf8');
const optionContainer=()=>({dataset:{},writes:0,set innerHTML(value){this.writes++;},querySelectorAll(){return [];}});
const els={modelOptions:optionContainer(),effortOptions:optionContainer(),modelNote:{}};
const catalog={models:[{id:'a',name:'A'},{id:'b',name:'B'}],efforts:[{id:'low',name:'Low'}]};
const selection={model:'a',effort:'low'};
const context=vm.createContext({els,modelCatalog:catalog,modelSelection:selection,esc:String,window:{},applyModel(){}});
vm.runInContext(source.slice(source.indexOf('function paintModelMenu()'),source.indexOf('async function loadModels(')),context);
vm.runInContext('paintModelMenu();paintModelMenu()',context);
assert.equal(els.modelOptions.writes,1,'background refresh must keep existing model buttons');
assert.equal(els.effortOptions.writes,1,'background refresh must keep existing effort buttons');
selection.model='b';vm.runInContext('paintModelMenu()',context);
assert.equal(els.modelOptions.writes,2);assert.equal(els.effortOptions.writes,1);
vm.runInContext(source.slice(source.indexOf('function positionControlMenu('),source.indexOf('controlMenus.forEach(({ chip, menu, paint })')),context);
for(const [width,height,x,y] of [[360,740,310,650],[430,400,200,160],[1280,850,400,740]]){
 context.window.visualViewport={offsetLeft:0,offsetTop:0,width,height};
 context.chip={getBoundingClientRect:()=>({left:x,top:y,bottom:y+44})};
 context.menu={style:{},getBoundingClientRect(){return {height:Math.min(260,parseFloat(this.style.maxHeight)||260)};}};
 vm.runInContext('positionControlMenu(chip,menu)',context);
 const style=context.menu.style,left=parseFloat(style.left),top=parseFloat(style.top);
 assert.ok(left>=8&&left+parseFloat(style.width)<=width-8);
 assert.ok(top>=8&&top+context.menu.getBoundingClientRect().height<=height-8);
 assert.equal(style.position,'fixed');
}
console.log('Control menus preserve touched options and fit mobile/desktop viewports');
