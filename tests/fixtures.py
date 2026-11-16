FAKES = {
 "pl": {"match_name": "Manchester United", "reg_open": "Mon 09 Nov, 10:00am", "reg_close": "Thu 19 Nov, 5:00pm",
      "links_sent": "Fri 20 Nov", "sales": [{"tier": "All Members", "open": "Fri 20 Nov, 10:00am", "close": "Fri 20 Nov, 6:00pm"},
      {"tier": "Season Ticket Holders and All Red Members with a Match Credit Balance of 4", "open": "Thu 19 Nov, 12:30am", "close": "TBA"},
      {"tier": "Wheelchair users", "open": "Fri 20 Nov, 9:00am", "close": "TBA"},
      {"tier": "Priority Rights Holders", "open": "Wed 18 Nov, 10:00am", "close": "TBA"}],
      "ballot_open": "Tue 17 Nov, 11:00am", "ballot_close": "Wed 18 Nov, 11:00am", "ballot_results": "Thu 26 Nov",
      "match_date": "Sat 21 Nov, 3:00pm"},
 "cup": {"match_name": "Spurs", "competition": "League Cup", "sales": [{"tier": "Credit balance of 2", "open": "Mon 23 Nov, 11:00am", "close": "Tue 24 Nov, 12:30am"}],
      "ballot_open": "Fri 20 Nov, 2:00pm", "ballot_close": "Sun 22 Nov, 11:00am", "ballot_results": "TBA",
      "acs_start": "Tue 01 Dec", "acs_end": "Wed 02 Dec", "match_date": "Wed 16 Dec, 8:00pm"},
 "away": {"match_name": "Everton", "sales": [{"tier": "9+ Away Credit Balance", "open": "Mon 23 Nov, 9:00am", "close": "Mon 23 Nov, 5:00pm"},
      {"tier": "Away Match Credit 5", "open": "Tue 24 Nov, 9:00am", "close": "TBA"}],
      "forwarding_deadline": "Wed 25 Nov, 1:00pm", "match_date": "Sat 05 Dec, 12:30pm",
      "allocation": "2,962 tickets", "prices": ["Adults £30", "Over 65s £22", "Under 18s £15"]},
 "cl": {"match_name": "LASK", "sales": [{"tier": "9+ Games", "open": "Wed 25 Nov, 8:15am", "close": "Thu 26 Nov, 7:30am", "info": "Guaranteed Sale", "forwarding_deadline": "Thu 26 Nov, 11:00am"},
      {"tier": "5+ Games", "open": "Fri 27 Nov, 8:15am", "close": "Fri 27 Nov, 4:00pm", "info": "Subject to availability", "forwarding_deadline": "TBA"}],
      "match_date": "Wed 09 Dec, 5:45pm", "allocation": "TBA", "prices": ["Adults €45"]},
}
def pick(prompt):
    if "Premier League ticket page" in prompt: return FAKES["pl"]
    if "Cup or European Home" in prompt: return FAKES["cup"]
    if "Champions League / European Away" in prompt: return FAKES["cl"]
    if "LFC Away match" in prompt: return FAKES["away"]
    raise ValueError("unknown prompt")
